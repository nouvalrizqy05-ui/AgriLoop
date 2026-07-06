"""
backtest_climate.py
-------------------
Reader untuk data/historical_climate.csv — iklim growing-season per provinsi
per tahun (NASA POWER), dipakai oleh predictions_router._build_backtest untuk
menjalankan ulang model di tiap tahun historis (real backtest).

Dibangun oleh scripts/fetch_backtest_climate.py. Lihat docstring script itu
untuk detail agregasi (rainfall = 30-day-equivalent agar konsisten dengan
period_days=30 di prediksi live).
"""

import csv
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).parent / "data" / "historical_climate.csv"
NDVI_CSV_PATH = Path(__file__).parent / "data" / "historical_ndvi.csv"


@lru_cache(maxsize=1)
def _load() -> dict[tuple[str, int], dict]:
    """Map (province_code, year) -> {rainfall_mm, temperature_c, solar_radiation}."""
    if not CSV_PATH.exists():
        logger.warning(f"historical_climate.csv tidak ditemukan: {CSV_PATH} — backtest hanya aktual")
        return {}
    out: dict[tuple[str, int], dict] = {}
    with open(CSV_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[(str(r["code"]), int(r["year"]))] = {
                    "rainfall_mm":     float(r["rainfall_mm"]),
                    "temperature_c":   float(r["temperature_c"]),
                    "solar_radiation": float(r["solar_radiation"]),
                }
            except (KeyError, ValueError) as e:
                logger.warning(f"Baris historical_climate dilewati: {r} ({e})")
    logger.info(f"historical_climate loaded: {len(out)} (provinsi x tahun)")
    return out


def annual_climate(province_code: str, year: int) -> dict | None:
    """Iklim tahunan satu provinsi, atau None kalau tidak ada di snapshot."""
    return _load().get((str(province_code), int(year)))


@lru_cache(maxsize=1)
def _load_ndvi() -> dict[tuple[str, int], float]:
    """Map (province_code, year) -> NDVI growing-season real (MODIS MOD13Q1)."""
    if not NDVI_CSV_PATH.exists():
        logger.warning(f"historical_ndvi.csv tidak ditemukan: {NDVI_CSV_PATH} — backtest pakai NDVI baseline")
        return {}
    out: dict[tuple[str, int], float] = {}
    with open(NDVI_CSV_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[(str(r["code"]), int(r["year"]))] = float(r["ndvi"])
            except (KeyError, ValueError) as e:
                logger.warning(f"Baris historical_ndvi dilewati: {r} ({e})")
    logger.info(f"historical_ndvi loaded: {len(out)} (provinsi x tahun)")
    return out


def annual_ndvi(province_code: str, year: int) -> float | None:
    """NDVI growing-season satu provinsi-tahun, atau None kalau tidak ada."""
    return _load_ndvi().get((str(province_code), int(year)))
