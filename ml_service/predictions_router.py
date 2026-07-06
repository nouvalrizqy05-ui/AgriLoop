"""
predictions_router.py
---------------------
Endpoints prediksi pangan per KABUPATEN/KOTA untuk dashboard pemerintah.

Mekanisme (real model):
  - Master wilayah (kabupaten/kota) dibaca dari Supabase (tabel `kabupaten`);
    baseline & luas panen dari `kabupaten_produksi` (data BPS, mis. SIMDASI).
  - Saat request, untuk tiap kabupaten:
      1) fetch iklim dari NASA POWER (lewat data_cache; di-cache 6 jam),
      2) panggil model (predict / predict_yield_batch) yang sudah di-train,
      3) surplus_pct = (yield_pred - baseline) / baseline * 100 (baseline = rata-
         rata yield <=3 tahun terakhir kabupaten itu; fallback baseline provinsi).
  - Jika model belum loaded / fetch iklim gagal → fallback baseline + jitter
    deterministik (peta tetap demo-able offline).

Mode: 'ALL' → peta nasional per provinsi; nama provinsi → drill-down ke
kabupaten/kota provinsi itu. NDVI series detail = cache APPEEARS (real MODIS)
atau estimator musiman; backtest = aktual BPS vs model dijalankan ulang.
"""

import asyncio
import hashlib
import logging
import math
from datetime import date, timedelta
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text, bindparam
from sqlalchemy.orm import Session

from database import get_db, PredictionLog, TrainingFeedback  # noqa: F401
from data_cache import get_or_fetch_climate, get_cached_climate_batch, _bg_refresh_climate
from data_fetcher import INDONESIA_DEFAULTS, estimate_ndvi_from_season
from model import (
    is_model_loaded,
    predict as ml_predict,
    predict_yield_only,
    predict_yield_batch,
    BASE_YIELD,
)
import backtest_climate
import kementan_data
import provinces_data
from schemas import (
    CropType,
    KabupatenDetail,
    KabupatenPrediction,
    NdviPoint,
    PredictInput,
    PredictionsResponse,
    YieldPoint,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


# Catatan: level analisis = KABUPATEN/KOTA. Master wilayah dibaca dari Supabase
# (tabel `kabupaten`), data produksi/baseline dari `kabupaten_produksi` (BPS).


# ── NDVI ESTIMATOR ─────────────────────────────────────
# NDVI baseline per komoditas (rata-rata growing season, sumber: literatur
# remote sensing pertanian tropis & nilai default fallback domain knowledge).
_BASE_NDVI: dict[str, float] = {
    "padi":         0.62,   # padi irigasi: NDVI tinggi & stabil
    "jagung":       0.58,
    "kedelai":      0.55,
    "ubi_jalar":    0.60,
    "ubi_kayu":     0.65,   # siklus panjang, kanopi rapat
    "cabe_besar":   0.55,
    "cabe_rawit":   0.50,
    "bawang_merah": 0.45,   # daun kecil, kanopi rendah
    "bawang_putih": 0.45,
}


async def _ndvi_series_for_detail(
    lat: float,
    lon: float,
    crop_type: str,
    start: date,
    n_months: int,
    db: Session,
) -> tuple[list[NdviPoint], str]:
    """
    Series NDVI untuk endpoint detail.

    Strategi:
      1) Coba cache APPEEARS time-series (period_days=-2). Kalau hit → real.
      2) Kalau miss & APPEEARS creds tersedia → submit task background.
         Tapi karena 3-10 menit, untuk request ini fallback dulu.
      3) Selalu siap dengan synthetic estimator sebagai fallback.

    Returns: (list[NdviPoint], source) di mana source =
        "modis_appeears" | "seasonal_estimate".
    """
    try:
        from ndvi_fetcher import get_or_fetch_ndvi_series
    except ImportError:
        get_or_fetch_ndvi_series = None  # type: ignore

    # Coba ambil cache APPEEARS (non-blocking — kalau cache miss, fungsi
    # akan submit task ke APPEEARS yang lambat. Kita pakai force_refresh=False
    # dan tunggu cuma kalau cache HIT supaya request tetap responsif.)
    if get_or_fetch_ndvi_series is not None:
        try:
            from data_cache import get_cached_climate
            SERIES_PERIOD_SENTINEL = -2
            cached = get_cached_climate(db, lat, lon, period_days=SERIES_PERIOD_SENTINEL)
            if cached and cached.get("series"):
                points = [
                    NdviPoint(date=p["date"], ndvi=p["ndvi"])
                    for p in cached["series"]
                ]
                logger.info(
                    f"NDVI series HIT cache APPEEARS ({lat},{lon}): {len(points)} titik"
                )
                return points, "modis_appeears"
        except Exception as e:
            logger.warning(f"Cek cache NDVI series gagal: {e}")

    # Fallback: synthetic estimator
    return _estimate_ndvi_series_synthetic(lat, lon, crop_type, start, n_months), "seasonal_estimate"


def _estimate_ndvi_series_synthetic(
    lat: float,
    lon: float,
    crop_type: str,
    start: date,
    n_months: int,
) -> list[NdviPoint]:
    """
    Generate NDVI bulanan realistis untuk Indonesia.

    Sumber sinyal:
      - Baseline per komoditas (irigasi vs tadah hujan vs hortikultura)
      - Pola monsun Indonesia: NDVI puncak Mar-Mei (pasca puncak hujan
        Jan-Feb), trough Sep-Okt (puncak kemarau). Vegetasi lag ~1 bulan
        di belakang curah hujan.
      - Variasi inter-annual ~3 tahun (proxy siklus ENSO)
      - Jitter per-koordinat (hash deterministik) supaya tiap wilayah
        punya pola unik tapi reproducible.

    Bukan data satelit real - APPEEARS/Sentinel-2 punya pipeline terpisah
    di ndvi_fetcher.py (butuh NASA Earthdata credentials + queue 1-5 menit
    per task, tidak feasible untuk endpoint sync). Estimator ini dipakai
    untuk visualisasi tren historis pada dashboard pemerintah.
    """
    base = _BASE_NDVI.get(crop_type, 0.55)

    # Deterministic per-location seed (stabil per koordinat).
    loc_seed = int(
        hashlib.md5(f"{lat:.3f},{lon:.3f}".encode()).hexdigest()[:8], 16
    )

    series: list[NdviPoint] = []
    for i in range(n_months):
        d = start + timedelta(days=30 * i)
        month = d.month
        year  = d.year

        # Pola monsun: cos peak di bulan 4 (April), trough di bulan 10 (Okt)
        phase    = (month - 4) * math.pi / 6
        seasonal = 0.16 * math.cos(phase)

        # Variasi tahun (siklus ~3 tahun)
        annual = 0.05 * math.sin((year - 2018) * math.pi / 1.5)

        # Per-lokasi + per-bulan noise dari hash (deterministik)
        bit_shift = (i * 7) % 24
        loc_var   = ((loc_seed >> bit_shift) & 0xff) / 4000.0 - 0.032

        ndvi = base + seasonal + annual + loc_var
        ndvi = max(0.15, min(0.92, ndvi))  # clamp realistic range

        series.append(NdviPoint(date=d.isoformat(), ndvi=round(ndvi, 3)))

    return series


def _status_from_surplus(surplus_pct: float) -> str:
    if surplus_pct > 10:
        return "surplus"
    if surplus_pct > -10:
        return "cukup"
    if surplus_pct > -20:
        return "waspada"
    return "defisit"


def _fallback_yield(commodity: str, region_id: str) -> float:
    """Yield baseline + jitter deterministik per region saat ML tak tersedia."""
    base = BASE_YIELD.get(commodity, 5.0)
    # Jitter +- 15% berdasarkan hash id (stabil antar request).
    seed = sum(ord(c) for c in str(region_id))
    jitter = ((seed % 31) - 15) / 100.0
    return round(base * (1 + jitter), 2)


def _kementan_baseline_yield(province: str, commodity: str) -> float | None:
    """
    Rata-rata yield 3 tahun terakhir dari Kementan untuk provinsi+komoditas.
    Dipakai sebagai baseline surplus_pct yang lebih akurat dibanding
    BASE_YIELD nasional generik.
    """
    rows = kementan_data.trend(province, commodity)
    if not rows:
        return None
    last3 = rows[-3:] if len(rows) >= 3 else rows
    return sum(r["yield_ton_per_ha"] for r in last3) / len(last3)


def _province_latest_luas(province: str, commodity: str) -> float | None:
    """Luas panen Kementan tahun terbaru utk provinsi+komoditas. None kalau kosong."""
    rows = kementan_data.trend(province, commodity)
    return rows[-1]["luas_panen_ha"] if rows else None


async def _predict_one(
    row: dict,
    commodity: CropType,
    db: Session,
    use_model: bool,
    baseline_yield: float | None = None,
    actual: dict | None = None,
    allow_stale: bool = False,
) -> KabupatenPrediction:
    """
    Prediksi 1 region (kabupaten/kota ATAU provinsi). Region row schema:
        {id, kabupaten, lat, lon, luas}

    `baseline_yield` overrides national baseline untuk hitung surplus_pct.
    Dipakai mode provinsi dengan baseline yield Kementan 3 tahun terakhir.

    `actual` = {"yield_actual": float, "count": int} dari laporan panen petani
    untuk region ini (kalau ada). Ditempel ke response tanpa mengubah angka
    prediksi model — frontend menampilkannya berdampingan.
    """
    base = baseline_yield if baseline_yield is not None else BASE_YIELD.get(commodity, 5.0)
    yield_pred: float
    src = "fallback"

    if use_model:
        try:
            climate = await get_or_fetch_climate(
                lat=row["lat"], lon=row["lon"], db=db, period_days=30,
                allow_stale=allow_stale,
            )
            data = PredictInput(
                crop_type=commodity,
                land_area_ha=row["luas"],
                rainfall_mm=climate["rainfall_mm"],
                temperature_c=climate["temperature_c"],
                solar_radiation=climate["solar_radiation"],
                # NDVI estimasi musiman per komoditas+lokasi (sadar wet/dry +
                # boost Jawa/Bali). Konsisten dgn NDVI saat training model;
                # lebih informatif dibanding nilai fixed. NDVI real per-pixel
                # butuh GEE/MODIS (pipeline terpisah di ndvi_fetcher).
                ndvi=estimate_ndvi_from_season(row["lat"], row["lon"], commodity),
                pest_pressure=0.0,
                variety="Lokal",
            )
            # baseline lokal (Kementan provinsi) → prediksi nempel ke level wilayah,
            # konsisten dengan yield model yang dilatih per-provinsi.
            result = ml_predict(data, baseline=base)
            yield_pred = round(result.yield_ton_per_ha, 2)
            src = result.model_source
        except Exception as e:
            logger.warning(f"predict {row['kabupaten']} gagal: {e} — pakai fallback")
            yield_pred = _fallback_yield(commodity, row["id"])
    else:
        yield_pred = _fallback_yield(commodity, row["id"])

    surplus_pct = round((yield_pred - base) / base * 100.0, 1)
    produksi = round(yield_pred * row["luas"])

    logger.debug(
        f"{row['kabupaten']}: yield={yield_pred} t/ha "
        f"(src={src}, base={base:.2f}, surplus={surplus_pct}%)"
    )

    return KabupatenPrediction(
        id=row["id"],
        kabupaten=row["kabupaten"],
        yield_pred_ton_per_ha=yield_pred,
        luas_panen_ha=row["luas"],
        produksi_pred_ton=produksi,
        surplus_pct=surplus_pct,
        status=_status_from_surplus(surplus_pct),
        yield_actual_ton_per_ha=actual["yield_actual"] if actual else None,
        feedback_count=actual["count"] if actual else 0,
    )


def _input_from_climate(row: dict, commodity: CropType, climate: dict) -> PredictInput:
    """Bangun PredictInput dari row region + iklim (sudah di-fetch). Tanpa I/O."""
    return PredictInput(
        crop_type=commodity,
        # luas=0 (kabupaten tanpa data produksi) → pakai netral 1000 ha untuk input
        # model (yield/ha hampir tak terpengaruh); produksi tetap dihitung dari
        # row["luas"] asli di _assemble_prediction (0 = jujur belum ada data).
        land_area_ha=row["luas"] or 1000.0,
        rainfall_mm=climate["rainfall_mm"],
        temperature_c=climate["temperature_c"],
        solar_radiation=climate["solar_radiation"],
        # NDVI musiman per komoditas+lokasi (lihat _predict_one) — tidak lagi
        # fixed 0.65, jadi tiap provinsi/komoditas variatif & sesuai musim.
        ndvi=estimate_ndvi_from_season(row["lat"], row["lon"], commodity),
        pest_pressure=0.0,
        variety="Lokal",
    )


def _assemble_prediction(
    row: dict,
    commodity: CropType,
    base: float,
    yield_pred: float,
    actual: dict | None = None,
) -> KabupatenPrediction:
    """Rakit KabupatenPrediction dari yield yang sudah dihitung (tanpa I/O)."""
    surplus_pct = round((yield_pred - base) / base * 100.0, 1)
    produksi = round(yield_pred * row["luas"])
    return KabupatenPrediction(
        id=row["id"],
        kabupaten=row["kabupaten"],
        yield_pred_ton_per_ha=yield_pred,
        luas_panen_ha=row["luas"],
        produksi_pred_ton=produksi,
        surplus_pct=surplus_pct,
        status=_status_from_surplus(surplus_pct),
        yield_actual_ton_per_ha=actual["yield_actual"] if actual else None,
        feedback_count=actual["count"] if actual else 0,
    )


def _province_row(province: str, commodity: str) -> dict | None:
    """
    Bangun 'row' provinsi dengan centroid + luas_panen Kementan terbaru.
    Return None kalau provinsi tidak dikenal atau tidak ada data Kementan.
    """
    prov = provinces_data.get(province)
    if not prov:
        return None

    year = kementan_data.latest_year_for(prov.kementan_name, commodity)
    if year is None:
        # Provinsi dikenal tapi tidak punya data komoditas itu — pakai luas default
        luas = 1000.0
    else:
        trend = kementan_data.trend(prov.kementan_name, commodity)
        latest = next((t for t in trend if t["year"] == year), None)
        luas = latest["luas_panen_ha"] if latest else 1000.0

    return {
        "id":        f"PROV_{prov.code}",
        "kabupaten": prov.name,
        "lat":       prov.lat,
        "lon":       prov.lon,
        "luas":      luas,
    }


def _kabupaten_rows(db: Session, provinsi_kode: str) -> list[dict]:
    """
    Baca kabupaten/kota satu provinsi dari Supabase (tabel `kabupaten`).

    Mengembalikan row dengan shape sama seperti _province_row supaya kompatibel
    dengan _input_from_climate / _assemble_prediction. `luas` = luas panen kab
    untuk komoditas (Phase 2: dari kabupaten_produksi); sementara 0.0 → produksi
    tampil 0 sampai data BPS per-kabupaten masuk (yield/ha tetap real).
    """
    rows = db.execute(
        text(
            "SELECT kode, nama, lat, lon FROM public.kabupaten "
            "WHERE provinsi_kode = :pk ORDER BY nama"
        ),
        {"pk": provinsi_kode},
    ).fetchall()
    return [
        {
            "id":        f"KAB_{r.kode}",
            "kode":      r.kode,
            "kabupaten": r.nama,
            "lat":       float(r.lat),
            "lon":       float(r.lon),
            "luas":      0.0,
        }
        for r in rows
    ]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Jarak great-circle (km) untuk cari kabupaten terdekat dari titik GPS."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _kab_centroids(db: Session) -> list[tuple[str, float, float]]:
    """(kode, lat, lon) tiap kabupaten/kota untuk mapping GPS → kabupaten."""
    rows = db.execute(text("SELECT kode, lat, lon FROM public.kabupaten")).fetchall()
    return [(r.kode, float(r.lat), float(r.lon)) for r in rows if r.lat is not None and r.lon is not None]


def _feedback_by_kabupaten(db: Session, commodity: str) -> dict[str, dict]:
    """
    Agregasi laporan panen petani (yield aktual) per kabupaten untuk komoditas.

    Feedback (`training_feedback`) tidak menyimpan kode wilayah, tapi koordinat
    GPS-nya ada di `prediction_log` (via prediction_log_id). Tiap feedback
    dipetakan ke kabupaten lewat centroid terdekat. Dipakai untuk menampilkan
    yield aktual berdampingan dengan prediksi (ground truth), TANPA mengubah
    angka prediksi model. Return {kode: {"yield_actual": rata2, "count": n}}.
    """
    rows = db.execute(
        text(
            "SELECT tf.actual_yield_ton_per_ha AS y, pl.lat AS lat, pl.lon AS lon "
            "FROM training_feedback tf "
            "JOIN prediction_log pl ON tf.prediction_log_id = pl.id "
            "WHERE tf.crop_type = :c AND tf.actual_yield_ton_per_ha > 0 "
            "AND pl.lat IS NOT NULL AND pl.lon IS NOT NULL"
        ),
        {"c": commodity},
    ).fetchall()
    if not rows:
        return {}
    cents = _kab_centroids(db)
    if not cents:
        return {}
    agg: dict[str, list[float]] = {}
    for fb in rows:
        nearest = min(cents, key=lambda c: _haversine_km(fb.lat, fb.lon, c[1], c[2]))
        agg.setdefault(nearest[0], []).append(float(fb.y))
    return {
        kode: {"yield_actual": round(sum(v) / len(v), 2), "count": len(v)}
        for kode, v in agg.items()
    }


def _kab_area_weights(db: Session, provinsi_kode: str) -> dict[str, float]:
    """
    Bobot alokasi per kabupaten = share luas panen PADI (data BPS) dalam provinsi.

    Padi punya data per-kabupaten paling lengkap (453 kab) → dipakai sebagai
    proxy "ukuran lahan pertanian" untuk membagi total provinsi komoditas yang
    belum punya angka per-kabupaten. Bobot dijumlahkan = 1. Kalau provinsi tak
    punya data padi kab sama sekali → bobot rata (1/n) atas semua kab di master.
    """
    rows = db.execute(
        text(
            "SELECT kode_kabupaten, SUM(luas_panen_ha) AS luas "
            "FROM public.kabupaten_produksi "
            "WHERE crop_type = 'padi' AND LEFT(kode_kabupaten, 2) = :pk "
            "GROUP BY kode_kabupaten"
        ),
        {"pk": provinsi_kode},
    ).fetchall()
    total = sum(float(r.luas) for r in rows if r.luas)
    if total > 0:
        return {r.kode_kabupaten: float(r.luas) / total for r in rows if r.luas}
    # Fallback: provinsi tanpa data padi kab → bagi rata ke semua kab di master.
    allk = db.execute(
        text("SELECT kode FROM public.kabupaten WHERE provinsi_kode = :pk"),
        {"pk": provinsi_kode},
    ).fetchall()
    n = len(allk)
    return {r.kode: 1.0 / n for r in allk} if n else {}


def _kab_produksi_map(db: Session, kodes: list[str], commodity: str) -> dict[str, dict]:
    """
    Per kabupaten: luas panen tahun terbaru + baseline yield (rata-rata <=3 tahun
    terakhir) dari `kabupaten_produksi` (data BPS). Dipakai untuk produksi total
    & surplus_pct level kabupaten. {kode: {"luas": float|None, "baseline": float|None}}.

    Komoditas yang BELUM punya angka per-kabupaten (mis. cabe/bawang) dialokasikan
    dari total provinsi Kementan: luas = luas_provinsi x bobot lahan kab (share
    padi), baseline = yield provinsi. Ini meniru pola data kab yang sudah ada
    (yield seragam provinsi, luas dibagi per-kab → jumlah kab = total provinsi),
    jadi peta/analisis konsisten dengan backtest dan tidak menampilkan 0 palsu.
    """
    if not kodes:
        return {}
    stmt = text(
        "SELECT kode_kabupaten, tahun, luas_panen_ha, yield_ton_per_ha "
        "FROM public.kabupaten_produksi "
        "WHERE crop_type = :c AND kode_kabupaten IN :kk "
        "ORDER BY kode_kabupaten, tahun"
    ).bindparams(bindparam("kk", expanding=True))
    rows = db.execute(stmt, {"c": commodity, "kk": list(kodes)}).fetchall()

    agg: dict[str, list] = {}
    for r in rows:
        agg.setdefault(r.kode_kabupaten, []).append(
            (r.tahun, r.luas_panen_ha, r.yield_ton_per_ha)
        )
    out: dict[str, dict] = {}
    for kode, ys in agg.items():
        ys.sort()
        last3 = [y for _, _, y in ys[-3:] if y is not None]
        out[kode] = {
            "luas":     ys[-1][1],
            "baseline": (sum(last3) / len(last3)) if last3 else None,
        }

    # Kab tanpa data riil untuk komoditas ini → alokasikan dari total provinsi.
    missing = [k for k in kodes if k not in out]
    if missing:
        prov = provinces_data.by_code(missing[0][:2])
        prov_luas = _province_latest_luas(prov.kementan_name, commodity) if prov else None
        prov_base = _kementan_baseline_yield(prov.kementan_name, commodity) if prov else None
        if prov and prov_luas and prov_base:
            weights = _kab_area_weights(db, prov.code)
            for kode in missing:
                w = weights.get(kode)
                if w:  # kab tanpa lahan padi (bobot 0) tetap jujur tanpa data
                    out[kode] = {"luas": prov_luas * w, "baseline": prov_base}
    return out


@router.get("", response_model=PredictionsResponse)
async def list_predictions(
    province: str = "DI Yogyakarta",
    commodity: CropType = "padi",
    season: str = "MT 2024-1",
    db: Session = Depends(get_db),
) -> PredictionsResponse:
    """
    Prediksi pangan per region.

    Mode:
      - 'ALL' / 'INDONESIA'  -> peta nasional, 1 titik per provinsi
      - nama provinsi        -> drill-down: semua kabupaten/kota provinsi itu

    Surplus_pct dihitung vs baseline yield kabupaten (rata-rata <=3 tahun
    terakhir dari kabupaten_produksi; fallback ke baseline provinsi).
    """
    use_model = is_model_loaded()
    if not use_model:
        logger.warning("Model belum dimuat — fallback baseline aktif")

    prov_key = (province or "").strip().upper()

    # ── MODE 1: National view (semua provinsi sekaligus) ──
    if prov_key in ("ALL", "INDONESIA", "NASIONAL"):
        # Dulu: 37 provinsi di-await sekuensial, tiap iterasi bisa fetch NASA POWER
        # live (~0.5-2s) + predict() penuh (~400ms, termasuk loop confidence 200
        # pohon) → blocking ~20-25s. Sekarang dibuat pure-CPU + 1 query DB:
        #   1) kumpulkan row + baseline (pandas, murah),
        #   2) batch-read SEMUA iklim dari cache dalam 1 query (termasuk stale),
        #   3) SATU panggilan predict_yield_batch utk semua provinsi (overhead
        #      sklearn .predict() per-baris ~45ms → bayar sekali, bukan 37x),
        #   4) koordinat yang miss → pakai default + refresh di background.
        # Tak ada network di jalur request → selalu cepat, walau cache dingin.
        rows_meta: list[tuple[dict, float | None, float]] = []  # (row, baseline, base)
        for prov in provinces_data.all_provinces():
            row = _province_row(prov.name, commodity)
            if not row:
                continue
            baseline = _kementan_baseline_yield(prov.kementan_name, commodity)
            base = baseline if baseline is not None else BASE_YIELD.get(commodity, 5.0)
            rows_meta.append((row, baseline, base))

        coords = [(r["lat"], r["lon"]) for r, _, _ in rows_meta]
        climate_map = get_cached_climate_batch(db, coords, period_days=30)

        missing: list[tuple[float, float]] = []
        batch_inputs: list[tuple[PredictInput, float | None]] = []
        for row, baseline, _base in rows_meta:
            key = (round(row["lat"], 2), round(row["lon"], 2))
            climate = climate_map.get(key)
            if climate is None:
                missing.append((row["lat"], row["lon"]))
                climate = {**INDONESIA_DEFAULTS, "data_source": "default_fallback"}
            batch_inputs.append((_input_from_climate(row, commodity, climate), baseline))

        if use_model:
            yields = predict_yield_batch(batch_inputs)
        else:
            yields = [_fallback_yield(commodity, row["id"]) for row, _, _ in rows_meta]

        items = [
            _assemble_prediction(row, commodity, base, yld)
            for (row, _baseline, base), yld in zip(rows_meta, yields)
        ]

        # Refresh koordinat yang belum ada di cache, non-blocking (isi untuk
        # request berikutnya). Hanya kalau ada event loop yang berjalan.
        for lat, lon in missing:
            try:
                asyncio.create_task(_bg_refresh_climate(lat, lon, 30))
            except RuntimeError:
                pass

        return PredictionsResponse(
            province="Indonesia",
            commodity=commodity,
            season=season,
            items=items,
        )

    # ── MODE 2: Drill-down provinsi → semua kabupaten/kota ─
    # Level utama produk = kabupaten. Pilih provinsi → prediksi per kab/kota-nya
    # (yield/ha real dari model; baseline & backtest pakai data provinsi sampai
    # kabupaten_produksi terisi). Pola batch sama dgn mode nasional → cepat.
    prov = provinces_data.get(province)
    if not prov:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Provinsi '{province}' tidak dikenal. "
                f"Gunakan nama lengkap (e.g. 'Jawa Barat'), kode (e.g. '32'), "
                f"atau 'ALL' untuk peta nasional."
            ),
        )

    prov_baseline = _kementan_baseline_yield(prov.kementan_name, commodity)
    kab_rows = _kabupaten_rows(db, prov.code)

    # Provinsi tanpa kabupaten di master (mis. pemekaran Papua) → fallback 1 row provinsi.
    if not kab_rows:
        row = _province_row(province, commodity)
        if not row:
            raise HTTPException(status_code=404, detail=f"Tidak ada data wilayah untuk '{province}'")
        pred = await _predict_one(row, commodity, db, use_model, prov_baseline)
        return PredictionsResponse(
            province=prov.name, commodity=commodity, season=season, items=[pred],
        )

    # Baseline & luas panen per kabupaten dari data BPS (kabupaten_produksi);
    # fallback ke baseline provinsi kalau kabupaten itu belum ada datanya.
    prod_map = _kab_produksi_map(db, [r["kode"] for r in kab_rows], commodity)
    fb_map = _feedback_by_kabupaten(db, commodity)  # yield aktual petani per kab
    bases: list[float] = []
    for r in kab_rows:
        info = prod_map.get(r["kode"], {})
        if info.get("luas"):
            r["luas"] = info["luas"]          # produksi = yield x luas panen real
        bases.append(info.get("baseline") or prov_baseline or BASE_YIELD.get(commodity, 5.0))

    coords = [(r["lat"], r["lon"]) for r in kab_rows]
    climate_map = get_cached_climate_batch(db, coords, period_days=30)

    missing: list[tuple[float, float]] = []
    batch_inputs: list[tuple[PredictInput, float | None]] = []
    for r, b in zip(kab_rows, bases):
        key = (round(r["lat"], 2), round(r["lon"], 2))
        climate = climate_map.get(key)
        if climate is None:
            missing.append((r["lat"], r["lon"]))
            climate = {**INDONESIA_DEFAULTS, "data_source": "default_fallback"}
        batch_inputs.append((_input_from_climate(r, commodity, climate), b))

    if use_model:
        yields = predict_yield_batch(batch_inputs)
    else:
        yields = [_fallback_yield(commodity, r["id"]) for r in kab_rows]

    items = [
        _assemble_prediction(r, commodity, b, y, fb_map.get(r["kode"]))
        for r, b, y in zip(kab_rows, bases, yields)
    ]

    for lat, lon in missing:
        try:
            asyncio.create_task(_bg_refresh_climate(lat, lon, 30))
        except RuntimeError:
            pass

    return PredictionsResponse(
        province=prov.name, commodity=commodity, season=season, items=items,
    )


@router.get("/history", summary="Riwayat prediksi petani")
def predictions_history(
    petani_id: str | None = None,
    lahan_id: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    Riwayat panggilan /api/predict.

    Filter:
      - `petani_id` -> hanya prediksi milik petani itu
      - `lahan_id`  -> hanya prediksi lahan itu (bisa dikombinasi dengan petani_id)
      - `limit`     -> max baris (default 50, clamp ke [1, 200])

    Diurutkan terbaru ke terlama.
    """
    limit = max(1, min(int(limit), 200))

    q = db.query(PredictionLog)
    if petani_id:
        q = q.filter(PredictionLog.petani_id == petani_id)
    if lahan_id:
        q = q.filter(PredictionLog.lahan_id == lahan_id)

    rows = q.order_by(PredictionLog.created_at.desc()).limit(limit).all()

    return {
        "petani_id": petani_id,
        "lahan_id":  lahan_id,
        "total":     len(rows),
        "items": [
            {
                "id":                    r.id,
                "petani_id":             r.petani_id,
                "lahan_id":              r.lahan_id,
                "crop_type":             r.crop_type,
                "land_area_ha":          r.land_area_ha,
                "ndvi":                  r.ndvi,
                "rainfall_mm":           r.rainfall_mm,
                "temperature_c":         r.temperature_c,
                "solar_radiation":       r.solar_radiation,
                "pred_harvest_days":     r.pred_harvest_days,
                "pred_yield_ton_per_ha": r.pred_yield_ton_per_ha,
                "pred_risk_level":       r.pred_risk_level,
                "confidence":            r.pred_confidence,
                "model_source":          r.model_source,
                "feedback_given":        r.feedback_given,
                "lahan_archived":        bool(r.lahan_archived),
                "created_at":            r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


def _backtest_from_actuals(
    actuals: tuple[tuple[int, float, float | None], ...],
    commodity: str,
    province_code: str | None,
) -> tuple[tuple[tuple[int, float, str], ...], float | None, int | None]:
    """
    Backtest model vs aktual (generik). `actuals` = ((tahun, yield, luas_panen), ...)
    urut tahun naik. Untuk tiap tahun, jalankan ulang model dengan iklim NASA POWER
    tahunan (proxy via province_code) lalu bandingkan ke yield aktual. Baseline
    per tahun = leave-one-out (kausal). Return (rows, mape, next_year).
    """
    if not actuals:
        return (), None, None

    last5 = actuals[-5:]
    use_model = is_model_loaded()
    rows: list[tuple[int, float, str]] = []
    errors: list[float] = []
    yearly = [(y, yld) for (y, yld, _) in actuals if yld]

    def _loo_baseline(year: int) -> float | None:
        others = [v for (yr, v) in yearly if yr != year]
        return sum(others) / len(others) if others else None

    for (year, yld, luas) in last5:
        actual = round(yld, 2)
        rows.append((year, actual, "aktual"))

        climate = (
            backtest_climate.annual_climate(province_code, year)
            if province_code else None
        )
        if not (use_model and climate):
            continue
        try:
            ndvi = backtest_climate.annual_ndvi(province_code, year)
            data = PredictInput(
                crop_type=commodity,
                land_area_ha=luas or 1000.0,
                rainfall_mm=climate["rainfall_mm"],
                temperature_c=climate["temperature_c"],
                solar_radiation=climate["solar_radiation"],
                ndvi=ndvi if ndvi is not None else 0.65,
                pest_pressure=0.0,
                variety="Lokal",
            )
            pred = predict_yield_only(data, baseline=_loo_baseline(year))
            rows.append((year, pred, "prediksi"))
            if actual > 0:
                errors.append(abs(actual - pred) / actual)
        except Exception as e:
            logger.warning(f"backtest predict {province_code} {year} gagal: {e}")

    mape = round(sum(errors) / len(errors) * 100.0, 1) if errors else None
    return tuple(rows), mape, last5[-1][0] + 1


@lru_cache(maxsize=512)
def _historical_backtest(
    kementan_province_name: str,
    commodity: str,
    province_code: str | None,
) -> tuple[tuple[tuple[int, float, str], ...], float | None, int | None]:
    """
    Bagian deterministik backtest (dicache): untuk tiap tahun historis, jalankan
    ulang model dengan iklim NASA POWER tahun itu dan bandingkan ke yield aktual
    Kementan. Hasilnya statik per (provinsi, komoditas) — CSV iklim, data
    Kementan, dan model semuanya tetap — jadi aman dimemoize.

    Returns: (rows, mape, next_year) di mana rows = tuple of (year, value, kind).
    rows kosong + next_year None kalau Kementan tak punya data komoditas itu.
    """
    trend = kementan_data.trend(kementan_province_name, commodity)
    actuals = tuple(
        (t["year"], t["yield_ton_per_ha"], t.get("luas_panen_ha"))
        for t in trend if t.get("yield_ton_per_ha")
    )
    return _backtest_from_actuals(actuals, commodity, province_code)


def _build_backtest(
    kementan_province_name: str,
    commodity: str,
    province_code: str | None,
    predicted_yield: float,
) -> tuple[list[YieldPoint], float | None]:
    """
    Backtest model vs aktual + 1 titik proyeksi tahun depan.

    Bagian historis (aktual + prediksi per tahun + MAPE) dihitung sekali lalu
    dicache via _historical_backtest; di sini tinggal menempelkan titik proyeksi
    tahun depan dari prediksi live (yang berubah-ubah). Kalau Kementan tidak
    punya data, return ([], None) supaya frontend show empty state.
    """
    rows, mape, next_year = _historical_backtest(
        kementan_province_name, commodity, province_code
    )
    if not rows and next_year is None:
        return [], None

    points = [YieldPoint(year=y, value=v, kind=k) for (y, v, k) in rows]  # type: ignore[arg-type]
    points.append(
        YieldPoint(year=next_year, value=round(predicted_yield, 2), kind="prediksi")
    )
    return points, mape


def _build_backtest_kab(
    db: Session,
    kode_kabupaten: str,
    commodity: str,
    province_code: str | None,
    predicted_yield: float,
) -> tuple[list[YieldPoint], float | None]:
    """
    Backtest level kabupaten: aktual yield per tahun dari `kabupaten_produksi`
    (data BPS) vs model dijalankan ulang dengan iklim tahunan provinsi induk
    (proxy). + 1 titik proyeksi tahun depan dari prediksi live.
    """
    rows_db = db.execute(
        text(
            "SELECT tahun, yield_ton_per_ha, luas_panen_ha "
            "FROM public.kabupaten_produksi "
            "WHERE crop_type = :c AND kode_kabupaten = :k ORDER BY tahun"
        ),
        {"c": commodity, "k": kode_kabupaten},
    ).fetchall()
    actuals = tuple(
        (r.tahun, r.yield_ton_per_ha, r.luas_panen_ha)
        for r in rows_db if r.yield_ton_per_ha
    )
    # Komoditas belum ada di kabupaten_produksi (mis. cabe/bawang) → pakai backtest
    # provinsi sebagai proxy. Konsisten dgn alokasi: yield bersifat intensif (per ha),
    # alokasi luas tak mengubah kurva yield, jadi backtest kab = backtest provinsi.
    if not actuals:
        prov = provinces_data.by_code(province_code) if province_code else None
        if prov:
            return _build_backtest(
                prov.kementan_name, commodity, province_code, predicted_yield
            )
        return [], None
    rows, mape, next_year = _backtest_from_actuals(actuals, commodity, province_code)
    if not rows and next_year is None:
        return [], None

    points = [YieldPoint(year=y, value=v, kind=k) for (y, v, k) in rows]  # type: ignore[arg-type]
    points.append(
        YieldPoint(year=next_year, value=round(predicted_yield, 2), kind="prediksi")
    )
    return points, mape


@router.get("/{region_id}", response_model=KabupatenDetail)
async def get_detail(
    region_id: str,
    commodity: CropType = "padi",
    db: Session = Depends(get_db),
) -> KabupatenDetail:
    """
    Detail per region.

    region_id format:
      - "KAB_<kode>"  -> kabupaten/kota (lookup tabel kabupaten, 4-digit Kemendagri)
      - "PROV_<code>" -> provinsi (lookup provinces_data by kode)
    """
    kementan_province_name: str
    province_code: str | None = None
    row: dict | None = None
    actual: dict | None = None
    kab_baseline: float | None = None

    if region_id.startswith("KAB_"):
        # Mode kabupaten/kota (level utama). Backtest pakai aktual kabupaten
        # (kabupaten_produksi) + iklim provinsi induk sebagai proxy.
        kode = region_id.removeprefix("KAB_")
        r = db.execute(
            text(
                "SELECT kode, nama, lat, lon, provinsi_kode, provinsi_nama "
                "FROM public.kabupaten WHERE kode = :k"
            ),
            {"k": kode},
        ).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail=f"Kabupaten kode '{kode}' tidak ditemukan")
        row = {
            "id": region_id, "kode": r.kode, "kabupaten": r.nama,
            "lat": float(r.lat), "lon": float(r.lon), "luas": 0.0,
        }
        prov = provinces_data.by_code(r.provinsi_kode)
        kementan_province_name = prov.kementan_name if prov else (r.provinsi_nama or "")
        province_code = r.provinsi_kode
        info = _kab_produksi_map(db, [r.kode], commodity).get(r.kode, {})
        if info.get("luas"):
            row["luas"] = info["luas"]
        kab_baseline = info.get("baseline")
        actual = _feedback_by_kabupaten(db, commodity).get(r.kode)  # yield aktual petani
    elif region_id.startswith("PROV_"):
        # Mode provinsi
        code = region_id.removeprefix("PROV_")
        prov = provinces_data.by_code(code)
        if not prov:
            raise HTTPException(
                status_code=404,
                detail=f"Provinsi kode '{code}' tidak ditemukan",
            )
        row = _province_row(prov.name, commodity)
        kementan_province_name = prov.kementan_name
        province_code = prov.code
    else:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Region '{region_id}' tidak dikenal. "
                f"Gunakan format 'KAB_<kode>' atau 'PROV_<kode>'."
            ),
        )

    if row is None:
        raise HTTPException(status_code=404, detail=f"Tidak bisa load region {region_id}")

    baseline = (
        kab_baseline if kab_baseline is not None
        else _kementan_baseline_yield(kementan_province_name, commodity)
    )
    pred = await _predict_one(row, commodity, db, is_model_loaded(), baseline, actual)

    # NDVI series 7 tahun. Cache APPEEARS HIT -> real MODIS, MISS -> estimator.
    # Pre-warm via scripts/prewarm_ndvi_cache.py untuk dapat data real.
    series, ndvi_source = await _ndvi_series_for_detail(
        lat=row["lat"],
        lon=row["lon"],
        crop_type=commodity,
        start=date(2018, 1, 1),
        n_months=84,
        db=db,
    )

    if region_id.startswith("KAB_"):
        backtest, backtest_mape = _build_backtest_kab(
            db=db,
            kode_kabupaten=region_id.removeprefix("KAB_"),
            commodity=commodity,
            province_code=province_code,
            predicted_yield=pred.yield_pred_ton_per_ha,
        )
    else:
        backtest, backtest_mape = _build_backtest(
            kementan_province_name=kementan_province_name,
            commodity=commodity,
            province_code=province_code,
            predicted_yield=pred.yield_pred_ton_per_ha,
        )

    return KabupatenDetail(
        kabupaten=row["kabupaten"],
        yield_pred_ton_per_ha=pred.yield_pred_ton_per_ha,
        luas_panen_ha=row["luas"],
        total_produksi_ton=pred.produksi_pred_ton,
        ndvi_series=series,
        ndvi_source=ndvi_source,
        backtest=backtest,
        backtest_mape=backtest_mape,
        surplus_pct=pred.surplus_pct,
        status=pred.status,
        yield_actual_ton_per_ha=pred.yield_actual_ton_per_ha,
        feedback_count=pred.feedback_count,
    )
