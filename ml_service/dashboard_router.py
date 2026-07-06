"""
dashboard_router.py
-------------------
Endpoints untuk dashboard pemerintah (KPI tiles + tren produksi).

Sumber data real (v3.0 - tidak lagi hardcoded):
  - kementan_data.py        : Kementan produksi 2020-2025 untuk 38 provinsi x 9 komoditas
  - database.py        : prediction_log + training_feedback + model_version
                         (signal pemakaian aplikasi & akurasi model aktif)

Tile dibangun dari:
  - "Prediksi Produksi <crop>"  : produksi Kementan tahun terbaru utk provinsi
                                  yang diminta + delta YoY
  - "Akurasi Model"             : MAE yield + risk_accuracy dari ModelVersion
                                  aktif (DB)
  - "Prediksi Petani"           : COUNT(prediction_log) sebagai signal traksi
  - "Feedback Terkumpul"        : COUNT(training_feedback) untuk gauge retrain
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

import logging

import kementan_data
import provinces_data
import backtest_climate
from model import is_model_loaded, predict_yield_only
from database import (
    get_db,
    PredictionLog,
    TrainingFeedback,
    ModelVersion,
    get_latest_model_version,
)
from schemas import (
    DashboardSummary,
    KpiTile,
    PredictInput,
    YieldPoint,
    YieldTrend,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _fmt_ton(n: float) -> str:
    """Format produksi dengan satuan otomatis (jt ton / rb ton / ton)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f} jt ton"
    if n >= 1_000:
        return f"{n / 1_000:.1f} rb ton"
    return f"{n:.0f} ton"


def _fmt_delta(pct: float | None) -> tuple[str, bool]:
    """Format delta YoY ke string + positive flag."""
    if pct is None:
        return ("baseline", True)
    arrow = "+" if pct >= 0 else ""
    return (f"{arrow}{pct:.1f}% vs tahun lalu", pct >= 0)


@router.get("/summary", response_model=DashboardSummary)
def summary(
    province: str = "DI Yogyakarta",
    commodity: str = "padi",
    season: str = "Tahun berjalan",
    db: Session = Depends(get_db),
) -> DashboardSummary:
    """
    KPI dashboard pemerintah - SEMUA dari sumber real.

    Tile (berdasarkan provinsi + komoditas yang dipilih):
      1. Produksi Kementan terbaru (provinsi, komoditas)
      2. Delta YoY produksi total provinsi
      3. Akurasi model aktif (MAE yield + risk accuracy)
      4. Total prediksi aplikasi + feedback masuk
    """
    kementan_summary = kementan_data.summary(province)
    by_crop     = {row["crop_type"]: row for row in kementan_summary["by_crop"]}
    crop_row    = by_crop.get(commodity)
    crop_label  = commodity.replace("_", " ").title()

    if crop_row:
        produksi_str = _fmt_ton(crop_row["produksi_ton"])
        produksi_delta = (
            f"Kementan {crop_row['year']} - yield {crop_row['yield_ton_per_ha']:.2f} t/ha"
        )
        produksi_positive = True
        crop_year = crop_row["year"]
    else:
        produksi_str = "tidak ada data"
        produksi_delta = f"{province} - {commodity}"
        produksi_positive = False
        crop_year = kementan_summary["year_range"][1] or kementan_data.latest_year()

    # YoY: gunakan delta khusus komoditas yang dipilih
    delta_pct = kementan_data.yoy_delta_pct(province, commodity)
    yoy_str, yoy_positive = _fmt_delta(delta_pct)

    # Model accuracy dari DB
    ver = get_latest_model_version(db)
    if ver and ver.mae_yield is not None:
        accuracy_value = f"MAE {ver.mae_yield:.2f} t/ha"
        risk_pct = (
            f"risk {ver.risk_accuracy * 100:.1f}%"
            if ver.risk_accuracy is not None else f"v{ver.version}"
        )
        accuracy_positive = True
    else:
        accuracy_value = "belum ada"
        risk_pct = "jalankan train.py"
        accuracy_positive = False

    # Aktivitas aplikasi
    total_pred = db.query(func.count(PredictionLog.id)).scalar() or 0
    total_fb   = db.query(func.count(TrainingFeedback.id)).scalar() or 0
    fb_threshold = 10
    fb_remaining = max(0, fb_threshold - (total_fb % fb_threshold))
    fb_delta = (
        "siap retrain"
        if total_fb >= fb_threshold and fb_remaining == fb_threshold
        else f"{fb_remaining} lagi untuk retrain"
    )

    tiles = [
        KpiTile(
            label=f"Produksi {crop_label} {crop_year}",
            value=produksi_str,
            delta=produksi_delta,
            positive=produksi_positive,
        ),
        KpiTile(
            label=f"YoY {crop_label}",
            value=(f"{delta_pct:+.1f}%" if delta_pct is not None else "baseline"),
            delta=yoy_str,
            positive=yoy_positive,
        ),
        KpiTile(
            label="Akurasi Model",
            value=accuracy_value,
            delta=risk_pct,
            positive=accuracy_positive,
        ),
        KpiTile(
            label="Prediksi Aplikasi",
            value=f"{total_pred:,}".replace(",", "."),
            delta=f"{total_fb} feedback masuk, {fb_delta}",
            positive=total_pred > 0,
        ),
    ]

    return DashboardSummary(
        province=province,
        season=season,
        tiles=tiles,
    )


def _project_next_year(
    province: str, commodity: str, rows: list[dict]
) -> tuple[int, float] | None:
    """Proyeksi produksi tahun depan dari model RandomForest.

    Cepat & tanpa network: iklim diambil dari snapshot CSV (backtest_climate),
    model sudah dimuat di memori. Return (proj_year, produksi_ton) atau None
    kalau model belum dimuat / data tak cukup.

    Luas panen proyeksi = rata-rata 3 tahun terakhir, supaya tahun terakhir yang
    masih provisional (laporan parsial) tidak menarik proyeksi terlalu rendah.
    """
    if not is_model_loaded() or not rows:
        return None
    prov = provinces_data.get(province)
    if not prov:
        return None

    last_year = rows[-1]["year"]
    # Iklim proxy: tahun aktual terakhir yang ada di CSV (mundur kalau perlu).
    climate = None
    for y in range(last_year, last_year - 5, -1):
        climate = backtest_climate.annual_climate(prov.code, y)
        if climate:
            break
    if not climate:
        return None

    luas_vals = [r["luas_panen_ha"] for r in rows[-3:] if r.get("luas_panen_ha")]
    if not luas_vals:
        return None
    luas = sum(luas_vals) / len(luas_vals)

    ndvi = backtest_climate.annual_ndvi(prov.code, last_year)
    # Baseline lokal = rata-rata yield 3 tahun terakhir provinsi ini, supaya
    # proyeksi nempel ke level wilayahnya (bukan baseline nasional).
    yld_vals = [r["yield_ton_per_ha"] for r in rows[-3:] if r.get("yield_ton_per_ha")]
    local_base = sum(yld_vals) / len(yld_vals) if yld_vals else None
    try:
        yield_pred = predict_yield_only(PredictInput(
            crop_type=commodity,
            land_area_ha=luas,
            rainfall_mm=climate["rainfall_mm"],
            temperature_c=climate["temperature_c"],
            solar_radiation=climate["solar_radiation"],
            ndvi=ndvi if ndvi is not None else 0.65,
            pest_pressure=0.0,
            variety="Lokal",
        ), baseline=local_base)
    except Exception as e:
        logger.warning(f"proyeksi trend {province}/{commodity} gagal: {e}")
        return None

    return last_year + 1, yield_pred * luas


@router.get("/trend", response_model=YieldTrend)
def trend(
    province: str = "DI Yogyakarta",
    commodity: str = "padi",
) -> YieldTrend:
    """
    Tren produksi Kementan (semua tahun = aktual) + 1 titik proyeksi model.

    Semua tahun yang dirilis Kementan ditampilkan apa adanya sebagai "aktual"
    (angka tahun terakhir bisa direvisi/provisional, tapi tetap data nyata —
    bukan dilabeli prediksi). Titik "prediksi" satu-satunya adalah proyeksi
    RandomForest untuk tahun BERIKUTNYA, jadi legenda "Prediksi Panen Cerdas"
    benar-benar prediksi model.

    Unit dipilih otomatis: kalau ada angka >= 1 jt ton -> "juta ton",
    kalau cuma puluhan/ratusan ribu -> "ribu ton".
    """
    rows = kementan_data.trend(province, commodity)

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Kementan tidak punya data untuk {commodity} di {province}. "
                f"Pilih komoditas/provinsi lain."
            ),
        )

    proj = _project_next_year(province, commodity, rows)

    prod_vals = [r["produksi_ton"] for r in rows]
    if proj:
        prod_vals.append(proj[1])
    max_prod = max(prod_vals)
    if max_prod >= 1_000_000:
        unit = "juta ton"
        divisor = 1_000_000
    else:
        unit = "ribu ton"
        divisor = 1_000

    points = [
        YieldPoint(
            year=r["year"],
            value=round(r["produksi_ton"] / divisor, 2),
            kind="aktual",
        )
        for r in rows
    ]
    if proj:
        points.append(YieldPoint(
            year=proj[0],
            value=round(proj[1] / divisor, 2),
            kind="prediksi",
        ))

    return YieldTrend(
        province=province,
        commodity=commodity,
        unit=unit,
        points=points,
    )
