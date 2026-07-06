"""
data_fetcher.py
---------------
Fetch data iklim real dari NASA POWER (suhu, curah hujan, radiasi).
Tidak perlu API key, gratis, dan reliable untuk demo.

Fallback otomatis ke nilai default jika API tidak tersedia.
"""

import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

# Nilai default per wilayah Indonesia jika semua API gagal
INDONESIA_DEFAULTS = {
    "temperature_c":   27.0,
    "rainfall_mm":     120.0,
    "solar_radiation": 185.0,
}


# ── MAIN FETCH FUNCTION ────────────────────────────────
async def fetch_climate_data(lat: float, lon: float, days_back: int = 30) -> dict:
    """
    Fetch data iklim rata-rata N hari terakhir untuk koordinat tertentu.

    Urutan prioritas:
      1. NASA POWER (REST API, tanpa token)
      2. Nilai default Indonesia (fallback aman)

    Args:
        lat: Latitude lahan (-11 s/d 6 untuk Indonesia)
        lon: Longitude lahan (95 s/d 141 untuk Indonesia)
        days_back: Jumlah hari ke belakang untuk dirata-rata

    Returns:
        dict dengan keys: temperature_c, rainfall_mm, solar_radiation, data_source
    """
    try:
        result = await _fetch_nasa_power(lat, lon, days_back)
        logger.info(f"NASA POWER berhasil untuk ({lat}, {lon})")
        return result
    except Exception as e:
        logger.warning(f"NASA POWER gagal: {e} — pakai nilai default Indonesia")
        return {**INDONESIA_DEFAULTS, "data_source": "default_fallback", "lat": lat, "lon": lon}


def fetch_climate_data_sync(lat: float, lon: float, days_back: int = 30) -> dict:
    """Versi synchronous dari fetch_climate_data (untuk script & retrain)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _fetch_nasa_power(lat, lon, days_back))
                return future.result(timeout=30)
        else:
            return loop.run_until_complete(_fetch_nasa_power(lat, lon, days_back))
    except Exception as e:
        logger.warning(f"fetch_climate_data_sync gagal: {e}")
        return {**INDONESIA_DEFAULTS, "data_source": "default_fallback", "lat": lat, "lon": lon}


# ── NASA POWER ─────────────────────────────────────────
async def _fetch_nasa_power(lat: float, lon: float, days_back: int = 30) -> dict:
    """
    Fetch dari NASA POWER API.
    Dokumentasi: https://power.larc.nasa.gov/docs/services/api/

    Parameter yang diambil:
      T2M             = Suhu udara 2m dari permukaan (°C)
      PRECTOTCORR     = Curah hujan terkoreksi (mm/hari)
      ALLSKY_SFC_SW_DWN = Radiasi matahari permukaan (MJ/m²/hari)
    """
    end_date   = datetime.today()
    start_date = end_date - timedelta(days=days_back)

    params = {
        "parameters": "T2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN",
        "community":  "AG",
        "longitude":  lon,
        "latitude":   lat,
        "start":      start_date.strftime("%Y%m%d"),
        "end":        end_date.strftime("%Y%m%d"),
        "format":     "JSON",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(NASA_POWER_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    props = data["properties"]["parameter"]

    # Filter nilai invalid (-999 = missing data di NASA POWER)
    temp_vals = [v for v in props["T2M"].values()             if v != -999]
    rain_vals = [v for v in props["PRECTOTCORR"].values()     if v != -999]
    rad_vals  = [v for v in props["ALLSKY_SFC_SW_DWN"].values() if v != -999]

    if not temp_vals:
        raise ValueError("NASA POWER tidak mengembalikan data suhu yang valid")

    # Suhu  = rata-rata harian
    # Hujan = total akumulasi periode (bukan rata-rata harian)
    # Radiasi = rata-rata harian
    return {
        "temperature_c":   round(sum(temp_vals) / len(temp_vals), 1),
        "rainfall_mm":     round(sum(rain_vals), 1) if rain_vals else INDONESIA_DEFAULTS["rainfall_mm"],
        "solar_radiation": round(sum(rad_vals) / len(rad_vals), 1) if rad_vals else INDONESIA_DEFAULTS["solar_radiation"],
        "data_source":     "nasa_power",
        "lat":             lat,
        "lon":             lon,
        "period_days":     days_back,
        "fetched_at":      datetime.utcnow().isoformat(),
    }


# ── DAILY SERIES (untuk halaman cuaca petani) ──────────
async def fetch_climate_daily(lat: float, lon: float, days_back: int = 7) -> list[dict]:
    """
    Fetch data iklim harian (bukan rata-rata) untuk N hari terakhir.
    Dipakai oleh halaman /petani/cuaca yang menampilkan ringkasan harian.

    Returns:
        list of {date, temperature_min, temperature_max, temperature_mean,
                 rainfall_mm, solar_radiation} — satu entry per hari.
        Empty list jika NASA POWER gagal.
    """
    end_date   = datetime.today()
    start_date = end_date - timedelta(days=days_back - 1)

    params = {
        "parameters": "T2M,T2M_MIN,T2M_MAX,PRECTOTCORR,ALLSKY_SFC_SW_DWN",
        "community":  "AG",
        "longitude":  lon,
        "latitude":   lat,
        "start":      start_date.strftime("%Y%m%d"),
        "end":        end_date.strftime("%Y%m%d"),
        "format":     "JSON",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(NASA_POWER_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"NASA POWER daily gagal: {e}")
        return []

    props = data.get("properties", {}).get("parameter", {})
    dates = sorted(props.get("T2M", {}).keys())  # YYYYMMDD strings

    series: list[dict] = []
    for d in dates:
        t_mean = props.get("T2M", {}).get(d)
        t_min  = props.get("T2M_MIN", {}).get(d)
        t_max  = props.get("T2M_MAX", {}).get(d)
        rain   = props.get("PRECTOTCORR", {}).get(d)
        rad    = props.get("ALLSKY_SFC_SW_DWN", {}).get(d)

        # Skip baris yang seluruhnya invalid (-999)
        if all(v == -999 or v is None for v in [t_mean, rain, rad]):
            continue

        series.append({
            "date":             f"{d[:4]}-{d[4:6]}-{d[6:]}",  # ISO format
            "temperature_min":  round(t_min,  1) if t_min  not in (None, -999) else None,
            "temperature_max":  round(t_max,  1) if t_max  not in (None, -999) else None,
            "temperature_mean": round(t_mean, 1) if t_mean not in (None, -999) else None,
            "rainfall_mm":      round(rain,   1) if rain   not in (None, -999) else 0.0,
            "solar_radiation":  round(rad,    1) if rad    not in (None, -999) else None,
        })

    return series


# ── FORECAST (Open-Meteo) untuk halaman cuaca petani ──
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


async def fetch_forecast_daily(lat: float, lon: float, days: int = 7) -> list[dict]:
    """
    PRAKIRAAN cuaca harian KE DEPAN dari Open-Meteo (gratis, tanpa API key).

    Beda dari fetch_climate_daily (NASA POWER = historis, lag 1-3 hari), ini
    ramalan sungguhan untuk `days` hari ke depan termasuk hari ini.

    Returns list of {date, temperature_min/max/mean, rainfall_mm,
    solar_radiation (MJ/m^2/hari, konsisten dgn NASA), weather_code (WMO)}.
    Empty list kalau gagal (pemanggil bisa fallback ke NASA POWER).
    """
    days = max(1, min(days, 16))  # Open-Meteo forecast max 16 hari
    params = {
        "latitude":  lat,
        "longitude": lon,
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "temperature_2m_mean,precipitation_sum,shortwave_radiation_sum"
        ),
        "forecast_days": days,
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Open-Meteo forecast gagal: {e}")
        return []

    daily = data.get("daily", {})
    times = daily.get("time", [])

    def col(key: str, i: int):
        arr = daily.get(key) or []
        return arr[i] if i < len(arr) else None

    out: list[dict] = []
    for i, d in enumerate(times):
        out.append({
            "date":             d,  # sudah ISO YYYY-MM-DD
            "temperature_min":  col("temperature_2m_min", i),
            "temperature_max":  col("temperature_2m_max", i),
            "temperature_mean": col("temperature_2m_mean", i),
            "rainfall_mm":      col("precipitation_sum", i) or 0.0,
            "solar_radiation":  col("shortwave_radiation_sum", i),  # MJ/m^2/hari
            "weather_code":     col("weather_code", i),
        })
    return out


# ── NDVI HELPER ────────────────────────────────────────
def estimate_ndvi_from_season(lat: float, lon: float, crop_type: str) -> float:
    """
    Estimasi NDVI berdasarkan musim tanam Indonesia.
    NDVI aktual idealnya dari MODIS/Sentinel — ini fallback berbasis domain knowledge.

    Indonesia:
      - Musim hujan (Okt–Mar): vegetasi lebih hijau → NDVI lebih tinggi
      - Musim kemarau (Apr–Sep): vegetasi lebih kering → NDVI lebih rendah

    Nilai sinkron dengan convert_kementan_to_training.NDVI_BASE dan
    predictions_router._BASE_NDVI — update ketiganya jika ada perubahan.
    """
    month = datetime.today().month
    is_wet_season = month in [10, 11, 12, 1, 2, 3]

    # (wet_ndvi, dry_ndvi) per komoditas
    # Hortikultura NDVI lebih rendah karena kanopi lebih kecil/jarang
    base_ndvi: dict[str, tuple[float, float]] = {
        "padi":         (0.72, 0.58),
        "jagung":       (0.65, 0.52),
        "kedelai":      (0.60, 0.48),
        "ubi_kayu":     (0.65, 0.52),
        "ubi_jalar":    (0.62, 0.50),
        "cabe_besar":   (0.55, 0.44),
        "cabe_rawit":   (0.52, 0.42),
        "bawang_merah": (0.45, 0.38),
        "bawang_putih": (0.42, 0.36),
    }

    wet, dry = base_ndvi.get(crop_type, (0.58, 0.48))
    ndvi = wet if is_wet_season else dry

    # Jawa & Bali: irigasi lebih baik → NDVI lebih stabil dan sedikit lebih tinggi
    # Hortikultura dapat koreksi lebih kecil (+0.02) karena kanopi tetap rendah
    is_java_bali = (-9 <= lat <= -5) and (105 <= lon <= 116)
    if is_java_bali:
        hortikultura = crop_type in ("cabe_besar", "cabe_rawit", "bawang_merah", "bawang_putih")
        ndvi += 0.02 if hortikultura else 0.05

    return round(min(ndvi, 0.95), 2)


# ── BULK FETCH untuk TRAINING DATA ─────────────────────
async def fetch_bulk_for_training(locations: list[dict], days_back: int = 90) -> list[dict]:
    """
    Fetch data iklim untuk banyak lokasi sekaligus (untuk seed training data).

    Args:
        locations: list of {"lat": float, "lon": float, "crop_type": str, "provinsi": str}
        days_back: periode historis dalam hari

    Returns:
        list of dict siap pakai sebagai training data
    """
    tasks   = [fetch_climate_data(loc["lat"], loc["lon"], days_back) for loc in locations]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    training_rows = []
    for loc, climate in zip(locations, results):
        if isinstance(climate, Exception):
            logger.warning(f"Gagal fetch untuk {loc}: {climate}")
            climate = {**INDONESIA_DEFAULTS, "data_source": "default_fallback"}

        crop = loc.get("crop_type", "padi")
        row  = {
            "ndvi":            estimate_ndvi_from_season(loc["lat"], loc["lon"], crop),
            "rainfall_mm":     climate["rainfall_mm"],
            "temperature_c":   climate["temperature_c"],
            "solar_radiation": climate["solar_radiation"],
            "land_area_ha":    loc.get("land_area_ha", 1.0),
            "crop_type":       crop,
            "provinsi":        loc.get("provinsi", "unknown"),
            "lat":             loc["lat"],
            "lon":             loc["lon"],
            "data_source":     climate.get("data_source", "nasa_power"),
        }
        training_rows.append(row)

    return training_rows


# ── CLI TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json

    async def main():
        print("🌍 Test fetch NASA POWER untuk beberapa lokasi Indonesia...\n")

        test_locations = [
            {"lat": -7.25, "lon": 112.75, "label": "Surabaya, Jawa Timur"},
            {"lat": -6.90, "lon": 107.60, "label": "Bandung, Jawa Barat"},
            {"lat": -8.50, "lon": 115.25, "label": "Bali"},
            {"lat":  3.60, "lon":  98.67, "label": "Medan, Sumatera Utara"},
            {"lat": -5.14, "lon": 119.43, "label": "Makassar, Sulawesi Selatan"},
        ]

        for loc in test_locations:
            result = await fetch_climate_data(loc["lat"], loc["lon"], days_back=30)
            print(f"📍 {loc['label']}")
            print(f"   Suhu rata-rata  : {result['temperature_c']} °C")
            print(f"   Curah hujan     : {result['rainfall_mm']} mm")
            print(f"   Radiasi matahari: {result['solar_radiation']} MJ/m²")
            print(f"   Sumber data     : {result['data_source']}")
            print()

    asyncio.run(main())
