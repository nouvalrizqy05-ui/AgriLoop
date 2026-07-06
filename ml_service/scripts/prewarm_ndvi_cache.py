"""
prewarm_ndvi_cache.py
----------------------
Pre-warm NDVI cache untuk semua koordinat penting:
  - 37 provinsi centroid (level utama; kabupaten di-warm on-demand saat request)

Dua mode cache yang bisa di-prewarm:
  1) Single-point NDVI (period_days=-1, TTL 24 jam)
     Dipakai oleh /api/predict saat user submit GPS koordinat.
  2) Time-series NDVI 2018-2025 (period_days=-2, TTL 7 hari)
     Dipakai oleh /api/predictions/{id} detail untuk grafik NDVI panjang.

Default = mode 1 saja. Pakai --with-series untuk juga prewarm time-series
(lebih lambat ~3x).

Run:
    cd ml_service
    python scripts/prewarm_ndvi_cache.py                 # single-point saja
    python scripts/prewarm_ndvi_cache.py --with-series   # + time series
    python scripts/prewarm_ndvi_cache.py --diy-only --with-series   # hanya provinsi DIY, paling cepat untuk demo

Cache TTL: single-point 24 jam, series 7 hari.

Optional flag:
    --diy-only       hanya provinsi DIY (kode 34)
    --with-series    juga prewarm time-series 2018-2025 (jauh lebih lama)
    --series-only    skip single-point, fetch hanya time-series
    --force          abaikan cache, refetch semua
    --concurrency=N  jumlah task paralel ke APPEEARS (default 5)
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Windows console (cp1252) tidak bisa cetak emoji — paksa stdout/stderr UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Load .env + setup path supaya bisa import dari ml_service root
HERE = Path(__file__).resolve().parent
ML_SERVICE = HERE.parent
sys.path.insert(0, str(ML_SERVICE))

from dotenv import load_dotenv
load_dotenv(ML_SERVICE / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("prewarm")


async def prewarm_one(sem, db, name: str, lat: float, lon: float,
                      crop_type: str, force: bool) -> tuple[str, str, float | None]:
    """Single-point NDVI prewarm (period_days=-1, TTL 24 jam)."""
    from ndvi_fetcher import get_or_fetch_ndvi

    async with sem:
        try:
            result = await get_or_fetch_ndvi(
                lat=lat, lon=lon, db=db,
                crop_type=crop_type, days_back=32,
                force_refresh=force,
            )
            return (name, result["ndvi_source"], result["ndvi"])
        except Exception as e:
            logger.warning(f"  ⚠ {name} gagal (single): {e}")
            return (name, "error", None)


async def prewarm_one_series(sem, db, name: str, lat: float, lon: float,
                             force: bool) -> tuple[str, str, int]:
    """Time-series NDVI prewarm 2018-2025 (period_days=-2, TTL 7 hari)."""
    from ndvi_fetcher import get_or_fetch_ndvi_series

    async with sem:
        try:
            result = await get_or_fetch_ndvi_series(
                lat=lat, lon=lon, db=db,
                start_year=2018, end_year=2025,
                force_refresh=force,
            )
            n = len(result.get("series", []))
            return (name, result.get("ndvi_source", "error"), n)
        except Exception as e:
            logger.warning(f"  ⚠ {name} gagal (series): {e}")
            return (name, "error", 0)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diy-only",       action="store_true")
    parser.add_argument("--with-series",    action="store_true",
                        help="Juga prewarm time-series 2018-2025 (lebih lambat)")
    parser.add_argument("--series-only",    action="store_true",
                        help="Skip single-point, hanya prewarm time-series")
    parser.add_argument("--force",          action="store_true")
    parser.add_argument("--concurrency",    type=int, default=5)
    args = parser.parse_args()

    # ── Setup DB session ──────────────────────────────────
    from database import init_db, SessionLocal
    init_db()
    db = SessionLocal()

    # ── Kumpulkan target (level provinsi; kabupaten di-warm on-demand) ────
    targets: list[tuple[str, float, float, str]] = []  # (name, lat, lon, crop_type)

    import provinces_data
    provs = provinces_data.all_provinces()
    if args.diy_only:
        provs = [p for p in provs if p.code == "34"]
    for p in provs:
        targets.append((f"PROV-{p.name}", p.lat, p.lon, "padi"))

    if not targets:
        logger.error("Tidak ada target — keluar.")
        return 1

    logger.info(f"Pre-warming NDVI cache untuk {len(targets)} koordinat")
    logger.info(f"Concurrency        : {args.concurrency}")
    logger.info(f"Force refresh      : {args.force}")
    logger.info(f"Modes              : "
                f"{'single ' if not args.series_only else ''}"
                f"{'series' if (args.with_series or args.series_only) else ''}")
    logger.info("")

    sem = asyncio.Semaphore(args.concurrency)

    # ── Pass 1: Single-point NDVI ────────────────────────
    if not args.series_only:
        logger.info("=" * 60)
        logger.info("PASS 1: single-point NDVI (period=-1, TTL 24 jam)")
        logger.info(f"Estimasi durasi : {len(targets) * 3 // args.concurrency} menit")
        logger.info("=" * 60)

        tasks = [
            prewarm_one(sem, db, name, lat, lon, crop_type, args.force)
            for name, lat, lon, crop_type in targets
        ]
        completed = 0
        for fut in asyncio.as_completed(tasks):
            name, source, ndvi = await fut
            completed += 1
            icon = "✓" if source == "modis_appeears" else "○" if source == "seasonal_estimate" else "✗"
            ndvi_str = f"{ndvi:.3f}" if ndvi is not None else "  -  "
            logger.info(f"  [{completed:2d}/{len(targets)}] {icon} {ndvi_str}  {source:20s}  {name}")

    # ── Pass 2: Time-series NDVI ─────────────────────────
    if args.with_series or args.series_only:
        logger.info("")
        logger.info("=" * 60)
        logger.info("PASS 2: time-series NDVI 2018-2025 (period=-2, TTL 7 hari)")
        logger.info(f"Estimasi durasi : {len(targets) * 6 // args.concurrency} menit (lebih lama)")
        logger.info("=" * 60)

        # Fresh semaphore — Pass 1 mungkin sudah selesai semua
        sem2  = asyncio.Semaphore(args.concurrency)
        tasks = [
            prewarm_one_series(sem2, db, name, lat, lon, args.force)
            for name, lat, lon, _crop in targets
        ]
        completed = 0
        for fut in asyncio.as_completed(tasks):
            name, source, n_points = await fut
            completed += 1
            icon = "✓" if source == "modis_appeears" else "✗"
            logger.info(f"  [{completed:2d}/{len(targets)}] {icon} {n_points:3d} titik  {source:20s}  {name}")

    db.close()

    logger.info("")
    logger.info("Selesai.")
    logger.info("  Single-point cache  -> dipakai /api/predict")
    if args.with_series or args.series_only:
        logger.info("  Time-series cache   -> dipakai /api/predictions/{id} (grafik NDVI 7 tahun)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
