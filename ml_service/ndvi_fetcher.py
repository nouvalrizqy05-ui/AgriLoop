"""
ndvi_fetcher.py
---------------
Fetch data NDVI real dari NASA APPEEARS API (MODIS MOD13Q1 v061).

Tidak perlu API key untuk submit task — cukup daftar akun gratis di:
  https://appeears.earthdatacloud.nasa.gov/

Cara kerja APPEEARS:
  1. Login → dapat token (berlaku 48 jam)
  2. Submit task → dapat task_id
  3. Poll status sampai "done" (biasanya 1–5 menit)
  4. Download hasil CSV → ambil nilai NDVI

Produk yang dipakai:
  MOD13Q1.061 — MODIS Terra Vegetation Indices 16-Day 250m
  Band: _250m_16_days_NDVI  (nilai 0–10000, dibagi 10000 = 0.0–1.0)

Fallback:
  Jika APPEEARS tidak tersedia atau timeout → estimasi dari musim
  (logika yang sudah ada di data_fetcher.py)

Environment variables (.env):
  APPEEARS_USER=email_kamu@gmail.com
  APPEEARS_PASS=password_kamu
"""

import asyncio
import httpx
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Path eksplisit — ml_service/.env. Tanpa ini, kalau uvicorn dijalankan dari
# project root (lihat ml_service/run.ps1) python-dotenv tidak ketemu .env.
load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

# ── KONSTANTA ──────────────────────────────────────────
APPEEARS_BASE     = "https://appeears.earthdatacloud.nasa.gov/api"
APPEEARS_USER     = os.getenv("APPEEARS_USER", "")
APPEEARS_PASS     = os.getenv("APPEEARS_PASS", "")

MODIS_PRODUCT     = "MOD13Q1.061"
MODIS_LAYER       = "_250m_16_days_NDVI"

# Polling: cek status tiap N detik, maksimal M kali.
# APPEEARS sering antri 10-15 menit saat server sibuk -> kasih ruang 20 menit.
POLL_INTERVAL_SEC = 15
POLL_MAX_ATTEMPTS = 80   # 80 × 15 detik = 20 menit maksimal

# Token di-cache di memory selama sesi berjalan
_token_cache: dict = {"token": None, "expires_at": 0.0}


# ── AUTH ───────────────────────────────────────────────
async def _get_token(client: httpx.AsyncClient) -> str:
    """
    Login ke APPEEARS dan dapat bearer token.
    Token di-cache agar tidak login ulang setiap request.
    Berlaku 48 jam — kita refresh setelah 47 jam.
    """
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    if not APPEEARS_USER or not APPEEARS_PASS:
        raise ValueError(
            "APPEEARS_USER dan APPEEARS_PASS belum diset di .env\n"
            "Daftar gratis di: https://appeears.earthdatacloud.nasa.gov/"
        )

    resp = await client.post(
        f"{APPEEARS_BASE}/login",
        auth=(APPEEARS_USER, APPEEARS_PASS),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    token = data["token"]
    _token_cache["token"]      = token
    _token_cache["expires_at"] = now + 47 * 3600   # refresh setelah 47 jam

    logger.info("APPEEARS login berhasil, token valid 47 jam")
    return token


# ── SUBMIT TASK ────────────────────────────────────────
async def _submit_task(
    client: httpx.AsyncClient,
    token: str,
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    task_name: str,
) -> str:
    """
    Submit point-sampling task ke APPEEARS.
    Mengembalikan task_id untuk di-poll statusnya.

    Args:
        start_date, end_date: format "MM-DD-YYYY"
    """
    payload = {
        "task_type": "point",
        "task_name": task_name,
        "params": {
            "dates": [{"startDate": start_date, "endDate": end_date}],
            "layers": [{"product": MODIS_PRODUCT, "layer": MODIS_LAYER}],
            "coordinates": [
                {"latitude": lat, "longitude": lon, "id": "lahan", "category": "ndvi"}
            ],
            "output": {"format": {"type": "csv"}, "projection": "geographic"},
        },
    }

    resp = await client.post(
        f"{APPEEARS_BASE}/task",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    task_id = resp.json()["task_id"]
    logger.info(f"APPEEARS task submitted: {task_id}")
    return task_id


# ── POLL STATUS ────────────────────────────────────────
async def _wait_for_task(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
) -> bool:
    """
    Poll status task sampai selesai atau timeout.
    Return True jika done, False jika timeout/error.
    """
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(POLL_MAX_ATTEMPTS):
        await asyncio.sleep(POLL_INTERVAL_SEC)

        resp = await client.get(
            f"{APPEEARS_BASE}/task/{task_id}",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        status = resp.json().get("status", "")

        logger.debug(f"APPEEARS task {task_id} status: {status} (attempt {attempt+1})")

        if status == "done":
            return True
        elif status in ("error", "deleted"):
            logger.warning(f"APPEEARS task gagal dengan status: {status}")
            return False

    logger.warning(f"APPEEARS task {task_id} timeout setelah {POLL_MAX_ATTEMPTS} percobaan")
    return False


# ── DOWNLOAD & PARSE HASIL ─────────────────────────────
async def _download_ndvi(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
) -> Optional[float]:
    """
    Download file CSV hasil task dan ambil rata-rata NDVI.
    MODIS NDVI disimpan sebagai integer (×10000), dibagi 10000 jadi 0.0–1.0.
    Nilai fill/invalid: -3000 (dibuang).
    """
    headers = {"Authorization": f"Bearer {token}"}

    # List file output
    resp = await client.get(
        f"{APPEEARS_BASE}/bundle/{task_id}",
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])

    # Cari file CSV hasil. APPEEARS naming: "<task_name>-MOD13Q1-061-results.csv"
    # — layer name tidak ada di filename, hanya di kolom CSV.
    ndvi_file = next(
        (
            f for f in files
            if f.get("file_name", "").endswith(".csv")
            and "results" in f["file_name"].lower()
        ),
        None,
    )
    if not ndvi_file:
        # Fallback: ambil .csv pertama
        ndvi_file = next(
            (f for f in files if f.get("file_name", "").endswith(".csv")),
            None,
        )
    if not ndvi_file:
        logger.warning(
            f"File CSV tidak ditemukan di bundle APPEEARS. "
            f"Files: {[f.get('file_name') for f in files]}"
        )
        return None

    # Download CSV
    file_id = ndvi_file["file_id"]
    dl_resp = await client.get(
        f"{APPEEARS_BASE}/bundle/{task_id}/{file_id}",
        headers=headers,
        timeout=60,
        follow_redirects=True,
    )
    dl_resp.raise_for_status()

    # Parse CSV langsung dari teks
    import io
    import csv

    lines = dl_resp.text.splitlines()
    reader = csv.DictReader(io.StringIO("\n".join(lines)))

    ndvi_col = None
    values   = []

    for row in reader:
        # Temukan kolom NDVI persis (bukan kolom turunan VI_Quality dll yang
        # juga mengandung "_250m_16_days_NDVI"). Kolom utama formatnya:
        #   "<product>_<version>__250m_16_days_NDVI"  (mis. MOD13Q1_061__...)
        if ndvi_col is None:
            ndvi_col = next(
                (k for k in row if k.endswith("_250m_16_days_NDVI")),
                None,
            )
            if ndvi_col is None:
                logger.warning(f"Kolom NDVI tidak ditemukan. Kolom: {list(row.keys())[:10]}...")
                return None

        raw = row.get(ndvi_col, "")
        try:
            # APPEEARS CSV mengirim NDVI sudah ter-scale ke 0.0-1.0 (float string).
            # Native MODIS HDF integer ×10000 — APPEEARS sudah handle scaling.
            val = float(raw)
            if -1.0 <= val <= 1.0 and val > 0.0:  # filter fill (-0.3) + air/awan
                values.append(val)
        except (ValueError, TypeError):
            continue

    if not values:
        logger.warning("Tidak ada nilai NDVI valid dalam hasil APPEEARS")
        return None

    ndvi_mean = round(sum(values) / len(values), 4)
    logger.info(f"NDVI dari APPEEARS: {ndvi_mean} (dari {len(values)} titik data)")
    return ndvi_mean


# ── MAIN FETCH FUNCTION ────────────────────────────────
async def fetch_ndvi(
    lat: float,
    lon: float,
    days_back: int = 32,
    crop_type: str = "padi",
) -> dict:
    """
    Fetch NDVI real untuk satu lokasi dari NASA APPEEARS (MODIS MOD13Q1).

    Args:
        lat, lon   : Koordinat lahan
        days_back  : Periode ke belakang (MODIS 16-hari, minimal 32 hari agar dapat ≥1 composite)
        crop_type  : Dipakai hanya jika fallback ke estimasi musiman

    Returns:
        dict dengan keys:
          ndvi         : nilai NDVI (0.0–1.0)
          ndvi_source  : "modis_appeears" atau "seasonal_estimate"
          n_samples    : jumlah titik data yang dirata-rata (0 jika estimasi)
    """
    from data_fetcher import estimate_ndvi_from_season  # fallback

    end_dt   = datetime.today()
    start_dt = end_dt - timedelta(days=days_back)

    start_str = start_dt.strftime("%m-%d-%Y")
    end_str   = end_dt.strftime("%m-%d-%Y")
    task_name = f"panencerdas_{lat}_{lon}_{end_dt.strftime('%Y%m%d')}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. Login
            token = await _get_token(client)

            # 2. Submit task
            task_id = await _submit_task(client, token, lat, lon, start_str, end_str, task_name)

            # 3. Tunggu selesai
            success = await _wait_for_task(client, token, task_id)
            if not success:
                raise RuntimeError("Task APPEEARS tidak selesai dalam batas waktu")

            # 4. Download & parse
            ndvi = await _download_ndvi(client, token, task_id)
            if ndvi is None:
                raise RuntimeError("Gagal mengambil nilai NDVI dari hasil APPEEARS")

        return {
            "ndvi":        ndvi,
            "ndvi_source": "modis_appeears",
            "n_samples":   1,  # rata-rata dari periode
            "lat":         lat,
            "lon":         lon,
        }

    except Exception as e:
        logger.warning(f"APPEEARS gagal untuk ({lat}, {lon}): {e} — pakai estimasi musiman")
        ndvi_estimated = estimate_ndvi_from_season(lat, lon, crop_type)
        return {
            "ndvi":        ndvi_estimated,
            "ndvi_source": "seasonal_estimate",
            "n_samples":   0,
            "lat":         lat,
            "lon":         lon,
        }


def fetch_ndvi_sync(
    lat: float,
    lon: float,
    days_back: int = 32,
    crop_type: str = "padi",
) -> dict:
    """Versi synchronous dari fetch_ndvi."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    fetch_ndvi(lat, lon, days_back, crop_type)
                )
                return future.result(timeout=700)  # 10 menit + buffer
        else:
            return loop.run_until_complete(fetch_ndvi(lat, lon, days_back, crop_type))
    except Exception as e:
        logger.warning(f"fetch_ndvi_sync error: {e}")
        from data_fetcher import estimate_ndvi_from_season
        return {
            "ndvi":        estimate_ndvi_from_season(lat, lon, crop_type),
            "ndvi_source": "seasonal_estimate",
            "n_samples":   0,
            "lat":         lat,
            "lon":         lon,
        }


# ── BULK FETCH untuk TRAINING DATA ─────────────────────
async def fetch_ndvi_bulk(locations: list[dict], days_back: int = 32) -> list[dict]:
    """
    Fetch NDVI untuk banyak lokasi sekaligus.
    APPEEARS tidak membatasi concurrent task, tapi kita batasi 5 sekaligus
    agar tidak kena rate limit.

    Args:
        locations: list of {"lat": float, "lon": float, "crop_type": str}

    Returns:
        list of dict hasil fetch_ndvi per lokasi (urutan sama dengan input)
    """
    semaphore = asyncio.Semaphore(5)

    async def _fetch_one(loc: dict) -> dict:
        async with semaphore:
            result = await fetch_ndvi(
                loc["lat"], loc["lon"],
                days_back=days_back,
                crop_type=loc.get("crop_type", "padi"),
            )
            return result

    tasks   = [_fetch_one(loc) for loc in locations]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output = []
    for loc, res in zip(locations, results):
        if isinstance(res, Exception):
            from data_fetcher import estimate_ndvi_from_season
            output.append({
                "ndvi":        estimate_ndvi_from_season(loc["lat"], loc["lon"], loc.get("crop_type", "padi")),
                "ndvi_source": "seasonal_estimate",
                "n_samples":   0,
                "lat":         loc["lat"],
                "lon":         loc["lon"],
            })
        else:
            output.append(res)

    return output


# ── INTEGRASI DENGAN data_cache.py ─────────────────────
async def get_or_fetch_ndvi(
    lat: float,
    lon: float,
    db,
    crop_type: str = "padi",
    days_back: int = 32,
    force_refresh: bool = False,
) -> dict:
    """
    Helper utama: coba ambil NDVI dari cache dulu (TTL 24 jam),
    jika miss → fetch dari APPEEARS → simpan ke cache.

    Struktur cache sama dengan climate cache — disimpan di tabel
    climate_cache dengan key yang mengandung prefix "ndvi_".
    """
    import json
    from datetime import timedelta
    from data_cache import get_cached_climate, save_climate_cache

    # Pakai period_days=0 sebagai penanda "ini data NDVI bukan iklim"
    # Cache key dibedakan lewat lat/lon rounded yang sama, period_days=-1
    NDVI_PERIOD_SENTINEL = -1

    if not force_refresh:
        cached = get_cached_climate(db, lat, lon, period_days=NDVI_PERIOD_SENTINEL)
        if cached and "ndvi" in cached:
            logger.debug(f"NDVI cache HIT untuk ({lat}, {lon})")
            return cached

    # Fetch dari APPEEARS
    result = await fetch_ndvi(lat, lon, days_back=days_back, crop_type=crop_type)

    # Simpan ke cache (TTL 24 jam — NDVI berubah perlahan)
    if result.get("ndvi_source") == "modis_appeears":
        save_climate_cache(
            db, lat, lon,
            data=result,
            period_days=NDVI_PERIOD_SENTINEL,
            ttl_hours=24,
        )

    return result


# ── TIME SERIES (multi-tahun) ──────────────────────────
async def _download_ndvi_series(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
) -> Optional[list[dict]]:
    """
    Download CSV hasil task lalu parse SEMUA row (bukan rata-rata).
    Returns: list[{"date": "YYYY-MM-DD", "ndvi": float}] urut ascending.
    """
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get(
        f"{APPEEARS_BASE}/bundle/{task_id}",
        headers=headers, timeout=15,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])

    ndvi_file = next(
        (
            f for f in files
            if f.get("file_name", "").endswith(".csv")
            and "results" in f["file_name"].lower()
        ),
        None,
    )
    if not ndvi_file:
        ndvi_file = next(
            (f for f in files if f.get("file_name", "").endswith(".csv")),
            None,
        )
    if not ndvi_file:
        logger.warning(
            f"File CSV time-series tidak ditemukan. "
            f"Files: {[f.get('file_name') for f in files]}"
        )
        return None

    file_id = ndvi_file["file_id"]
    dl_resp = await client.get(
        f"{APPEEARS_BASE}/bundle/{task_id}/{file_id}",
        headers=headers, timeout=60, follow_redirects=True,
    )
    dl_resp.raise_for_status()

    import io
    import csv
    reader = csv.DictReader(io.StringIO(dl_resp.text))

    ndvi_col = None
    date_col = None
    points: list[dict] = []

    for row in reader:
        if ndvi_col is None:
            # Cari kolom NDVI utama (akhiran tepat, hindari VI_Quality_*)
            ndvi_col = next(
                (k for k in row if k.endswith("_250m_16_days_NDVI")),
                None,
            )
            date_col = next(
                (k for k in row if k.lower() in ("date", "datestamp", "modis_date")),
                None,
            )
            if not ndvi_col or not date_col:
                logger.warning(
                    f"Kolom ndvi/date tidak ditemukan. Kolom: {list(row.keys())[:10]}..."
                )
                return None

        try:
            val = float(row[ndvi_col])
            if not (-1.0 <= val <= 1.0) or val <= 0.0:
                continue
            points.append({
                "date": row[date_col].strip(),
                "ndvi": round(val, 4),
            })
        except (ValueError, TypeError, KeyError):
            continue

    if not points:
        return None

    # Sort by date string (ISO format string-sorts correctly)
    points.sort(key=lambda p: p["date"])
    return points


async def fetch_ndvi_series(
    lat: float,
    lon: float,
    start_year: int = 2018,
    end_year: int = 2025,
) -> dict:
    """
    Fetch time series NDVI MODIS dari APPEEARS untuk 1 koordinat,
    rentang `start_year`–`end_year` (inklusif).

    Returns:
        {
          "ndvi_source": "modis_appeears" | "error",
          "series":      [{"date": "YYYY-MM-DD", "ndvi": float}, ...],
          "lat": float,
          "lon": float,
        }

    MODIS MOD13Q1 16-hari → ~23 titik per tahun → ~160 titik untuk 7 tahun.
    Pemanggil yang ingin monthly bisa resample sendiri.

    Catatan: task ini bisa makan 3-10 menit (rentang besar). Pakai cache
    keras di pemanggil — series 7 tahun stabil, cukup di-fetch sekali per
    koordinat per tahun.
    """
    start_str = f"01-01-{start_year}"
    end_str   = f"12-31-{end_year}"
    task_name = f"panencerdas_series_{lat}_{lon}_{start_year}_{end_year}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            token = await _get_token(client)
            task_id = await _submit_task(client, token, lat, lon, start_str, end_str, task_name)
            ok = await _wait_for_task(client, token, task_id)
            if not ok:
                raise RuntimeError("APPEEARS task time-series tidak selesai")
            points = await _download_ndvi_series(client, token, task_id)
            if not points:
                raise RuntimeError("CSV time-series kosong")

        logger.info(
            f"NDVI series APPEEARS: {len(points)} titik untuk ({lat}, {lon}) "
            f"{start_year}-{end_year}"
        )
        return {
            "ndvi_source": "modis_appeears",
            "series":      points,
            "lat":         lat,
            "lon":         lon,
        }
    except Exception as e:
        logger.warning(f"APPEEARS series gagal untuk ({lat}, {lon}): {e}")
        return {
            "ndvi_source": "error",
            "series":      [],
            "lat":         lat,
            "lon":         lon,
        }


async def get_or_fetch_ndvi_series(
    lat: float,
    lon: float,
    db,
    start_year: int = 2018,
    end_year: int = 2025,
    force_refresh: bool = False,
) -> dict:
    """
    Time-series NDVI dengan cache. TTL 7 hari karena seri historis
    multi-tahun praktis stabil — composite lama jarang di-revisi.

    Cache di tabel climate_cache, key dibedakan dengan period_days = -2.
    """
    from data_cache import get_cached_climate, save_climate_cache

    SERIES_PERIOD_SENTINEL = -2
    if not force_refresh:
        cached = get_cached_climate(db, lat, lon, period_days=SERIES_PERIOD_SENTINEL)
        if cached and cached.get("series"):
            logger.debug(f"NDVI series cache HIT untuk ({lat}, {lon})")
            return cached

    result = await fetch_ndvi_series(lat, lon, start_year, end_year)
    if result["ndvi_source"] == "modis_appeears" and result["series"]:
        save_climate_cache(
            db, lat, lon,
            data=result,
            period_days=SERIES_PERIOD_SENTINEL,
            ttl_hours=24 * 7,   # 7 hari, series jarang berubah
        )
    return result


# ── PATCH fetch_bulk_for_training ──────────────────────
async def fetch_bulk_for_training_with_ndvi(
    locations: list[dict],
    days_back: int = 90,
) -> list[dict]:
    """
    Versi upgrade dari data_fetcher.fetch_bulk_for_training yang menyertakan
    NDVI real dari APPEEARS (bukan estimasi musiman).

    Gunakan fungsi ini di fetch_historical.py sebagai pengganti fetch_bulk_for_training.

    Args:
        locations: list of {"lat", "lon", "crop_type", "provinsi", "land_area_ha"}

    Returns:
        list of dict siap pakai sebagai training data (sama struktur dengan versi lama)
    """
    from data_fetcher import fetch_bulk_for_training, INDONESIA_DEFAULTS

    # Fetch iklim (suhu, hujan, radiasi) — seperti sebelumnya
    climate_rows = await fetch_bulk_for_training(locations, days_back=days_back)

    # Fetch NDVI real secara parallel
    logger.info(f"Fetching NDVI real untuk {len(locations)} lokasi dari APPEEARS...")
    ndvi_results = await fetch_ndvi_bulk(locations, days_back=32)

    # Gabungkan
    combined = []
    for climate_row, ndvi_result in zip(climate_rows, ndvi_results):
        row = {**climate_row}
        row["ndvi"]        = ndvi_result["ndvi"]
        row["ndvi_source"] = ndvi_result["ndvi_source"]
        combined.append(row)

        logger.info(
            f"  ({climate_row['lat']}, {climate_row['lon']}) "
            f"NDVI={ndvi_result['ndvi']} [{ndvi_result['ndvi_source']}]"
        )

    return combined


# ── CLI TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    async def main():
        print("🌿 Test fetch NDVI dari NASA APPEEARS\n")
        print("Pastikan APPEEARS_USER dan APPEEARS_PASS sudah diset di .env\n")

        test_locations = [
            {"lat": -7.25,  "lon": 112.75, "crop_type": "padi",   "label": "Surabaya, Jawa Timur"},
            {"lat": -6.90,  "lon": 107.60, "crop_type": "padi",   "label": "Bandung, Jawa Barat"},
            {"lat": -5.14,  "lon": 119.43, "crop_type": "jagung", "label": "Makassar, Sulawesi Selatan"},
        ]

        for loc in test_locations:
            print(f"📍 {loc['label']} ({loc['lat']}, {loc['lon']})")
            result = await fetch_ndvi(loc["lat"], loc["lon"], crop_type=loc["crop_type"])
            print(f"   NDVI         : {result['ndvi']}")
            print(f"   Sumber       : {result['ndvi_source']}")
            print(f"   Jumlah sample: {result['n_samples']}")
            print()

    asyncio.run(main())
