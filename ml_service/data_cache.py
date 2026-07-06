"""
data_cache.py
-------------
Cache hasil fetch iklim ke database lokal (SQLite/PostgreSQL).
Mencegah fetch berulang ke NASA POWER untuk koordinat yang sama.

TTL (Time-To-Live):
  - Data iklim       : 6 jam (cukup untuk prediksi harian)
  - Data historis    : 24 jam
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import Column, Integer, Float, String, DateTime, Text, Index, text, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from database import engine, SessionLocal  # pakai engine yang sudah ada di database.py

logger = logging.getLogger(__name__)

TTL_HOURS_CLIMATE   = 6
TTL_HOURS_HISTORICAL = 24


# ── MODEL TABEL CACHE ──────────────────────────────────
class CacheBase(DeclarativeBase):
    pass


class ClimateCache(CacheBase):
    """Cache data iklim per koordinat."""
    __tablename__ = "climate_cache"

    id           = Column(Integer, primary_key=True, index=True)
    lat          = Column(Float, nullable=False)
    lon          = Column(Float, nullable=False)
    lat_rounded  = Column(Float, nullable=False)  # dibulatkan 2 desimal untuk key lookup
    lon_rounded  = Column(Float, nullable=False)
    data_json    = Column(Text, nullable=False)    # hasil fetch sebagai JSON string
    data_source  = Column(String(30), nullable=False)
    period_days  = Column(Integer, nullable=False, default=30)
    fetched_at   = Column(DateTime, default=datetime.utcnow)
    expires_at   = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("idx_climate_coords_period", "lat_rounded", "lon_rounded", "period_days"),
    )


def init_cache():
    """Buat tabel cache jika belum ada."""
    CacheBase.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_climate_coords_period "
                "ON climate_cache (lat_rounded, lon_rounded, period_days)"
            ))
        logger.info("✅ Cache index siap")
    except Exception as e:
        logger.warning(f"Gagal membuat index cache: {e}")
    logger.info("✅ Cache table siap")


# ── GET FROM CACHE ─────────────────────────────────────
def get_cached_climate(
    db: Session,
    lat: float,
    lon: float,
    period_days: int = 30,
    allow_stale: bool = False,
) -> Optional[dict]:
    """
    Ambil data iklim dari cache.
    Koordinat dibulatkan 2 desimal (~1.1 km presisi) untuk mengurangi duplikasi.

    Args:
        allow_stale: kalau True, baris yang sudah expired tetap dikembalikan
            (dipakai untuk serve-stale-while-revalidate). Default False = hanya
            kembalikan baris yang masih valid.

    Returns:
        dict data iklim atau None jika tidak ada (atau expired saat allow_stale=False)
    """
    lat_r = round(lat, 2)
    lon_r = round(lon, 2)
    now   = datetime.utcnow()

    q = db.query(ClimateCache).filter(
        ClimateCache.lat_rounded == lat_r,
        ClimateCache.lon_rounded == lon_r,
        ClimateCache.period_days == period_days,
    )
    if not allow_stale:
        q = q.filter(ClimateCache.expires_at > now)

    cached = q.order_by(ClimateCache.fetched_at.desc()).first()

    if cached:
        is_stale = cached.expires_at <= now
        logger.debug(
            f"Cache {'STALE' if is_stale else 'HIT'} untuk ({lat_r}, {lon_r})"
        )
        return json.loads(cached.data_json)

    logger.debug(f"Cache MISS untuk ({lat_r}, {lon_r})")
    return None


def get_cached_climate_batch(
    db: Session,
    coords: list[tuple[float, float]],
    period_days: int = 30,
) -> dict[tuple[float, float], dict]:
    """
    Ambil iklim untuk BANYAK koordinat sekaligus dalam 1 query (termasuk baris
    stale/expired). Menghindari N+1 query saat membangun peta nasional.

    Key hasil = (lat_rounded, lon_rounded) dibulatkan 2 desimal. Untuk tiap
    koordinat dipilih baris dengan fetched_at terbaru. Koordinat yang tak punya
    baris cache sama sekali tidak muncul di dict (pemanggil yang menentukan
    fallback-nya).
    """
    if not coords:
        return {}

    wanted = {(round(la, 2), round(lo, 2)) for la, lo in coords}
    lat_set = {la for la, _ in wanted}
    lon_set = {lo for _, lo in wanted}

    rows = (
        db.query(ClimateCache)
        .filter(
            ClimateCache.period_days == period_days,
            ClimateCache.lat_rounded.in_(lat_set),
            ClimateCache.lon_rounded.in_(lon_set),
        )
        .order_by(ClimateCache.fetched_at.desc())
        .all()
    )

    # lat_set x lon_set bisa over-match (kartesian) — saring dgn `wanted` di sini.
    out: dict[tuple[float, float], dict] = {}
    for r in rows:
        key = (r.lat_rounded, r.lon_rounded)
        if key in wanted and key not in out:  # baris terbaru menang (order desc)
            out[key] = json.loads(r.data_json)
    return out


# ── SAVE TO CACHE ──────────────────────────────────────
def save_climate_cache(
    db: Session,
    lat: float,
    lon: float,
    data: dict,
    period_days: int = 30,
    ttl_hours: int = TTL_HOURS_CLIMATE,
) -> ClimateCache:
    """Simpan data iklim ke cache dengan TTL."""
    lat_r = round(lat, 2)
    lon_r = round(lon, 2)
    now   = datetime.utcnow()

    # Hapus cache lama untuk koordinat ini (bersihkan duplikasi)
    db.query(ClimateCache).filter(
        ClimateCache.lat_rounded == lat_r,
        ClimateCache.lon_rounded == lon_r,
        ClimateCache.period_days == period_days,
    ).delete(synchronize_session="fetch")

    cache_entry = ClimateCache(
        lat=lat,
        lon=lon,
        lat_rounded=lat_r,
        lon_rounded=lon_r,
        data_json=json.dumps(data),
        data_source=data.get("data_source", "unknown"),
        period_days=period_days,
        fetched_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
    )

    db.add(cache_entry)
    db.commit()
    db.refresh(cache_entry)
    logger.info(f"Cache SAVED untuk ({lat_r}, {lon_r}), expires in {ttl_hours}h")
    return cache_entry


# ── BACKGROUND REFRESH (serve-stale-while-revalidate) ──
import asyncio
import threading

_refresh_lock = threading.Lock()
_refreshing: set = set()  # koordinat yang sedang di-refresh -> cegah stampede


async def _bg_refresh_climate(lat: float, lon: float, period_days: int) -> None:
    """Fetch ulang iklim di belakang layar lalu simpan ke cache.

    Pakai session DB sendiri (bukan session request, yang sudah ditutup saat
    response terkirim). Dijaga 1 koordinat = 1 refresh aktif via _refreshing.
    """
    key = (round(lat, 2), round(lon, 2), period_days)
    with _refresh_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)
    try:
        from data_fetcher import fetch_climate_data
        data = await fetch_climate_data(lat, lon, period_days)
        if data.get("data_source") != "default_fallback":
            db2 = SessionLocal()
            try:
                save_climate_cache(db2, lat, lon, data, period_days)
            finally:
                db2.close()
    except Exception as e:
        logger.warning(f"Background refresh iklim gagal ({lat}, {lon}): {e}")
    finally:
        with _refresh_lock:
            _refreshing.discard(key)


# ── FETCH WITH CACHE (main helper) ────────────────────
async def get_or_fetch_climate(
    lat: float,
    lon: float,
    db: Session,
    period_days: int = 30,
    force_refresh: bool = False,
    allow_stale: bool = False,
) -> dict:
    """
    Helper utama: coba ambil dari cache dulu, jika miss → fetch dari NASA POWER → simpan ke cache.

    Args:
        lat, lon: koordinat lahan
        db: database session
        period_days: periode historis dalam hari
        force_refresh: paksa fetch ulang meskipun cache masih valid
        allow_stale: serve-stale-while-revalidate. Kalau cache valid tak ada tapi
            ada baris expired, kembalikan yang expired SEKARANG (instan) lalu
            refresh di belakang layar. Dipakai endpoint peta nasional supaya
            tak pernah blocking ~20-25s saat cache cold/expired.

    Returns:
        dict data iklim dengan keys: temperature_c, rainfall_mm, solar_radiation, data_source
    """
    from data_fetcher import fetch_climate_data  # import di sini untuk hindari circular

    # Cek cache valid dulu
    if not force_refresh:
        cached = get_cached_climate(db, lat, lon, period_days)
        if cached:
            return cached

        # Cache valid tak ada → kalau boleh stale, sajikan baris expired (instan)
        # dan jadwalkan refresh non-blocking. Hanya kalau ada loop yang jalan.
        if allow_stale:
            stale = get_cached_climate(db, lat, lon, period_days, allow_stale=True)
            if stale:
                try:
                    asyncio.create_task(_bg_refresh_climate(lat, lon, period_days))
                except RuntimeError:
                    pass  # tak ada running loop (mis. konteks sync) → lewati
                return stale

    # Fetch dari NASA POWER
    data = await fetch_climate_data(lat, lon, period_days)

    # Simpan ke cache (skip jika sumber adalah fallback default)
    if data.get("data_source") != "default_fallback":
        save_climate_cache(db, lat, lon, data, period_days)

    return data


def get_or_fetch_climate_sync(
    lat: float,
    lon: float,
    db: Session,
    period_days: int = 30,
    force_refresh: bool = False,
) -> dict:
    """Versi synchronous dari get_or_fetch_climate."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    get_or_fetch_climate(lat, lon, db, period_days, force_refresh)
                )
                return future.result(timeout=35)
        else:
            return loop.run_until_complete(
                get_or_fetch_climate(lat, lon, db, period_days, force_refresh)
            )
    except Exception as e:
        logger.warning(f"get_or_fetch_climate_sync error: {e}")
        from data_fetcher import INDONESIA_DEFAULTS
        return {**INDONESIA_DEFAULTS, "data_source": "default_fallback"}


# ── CLEANUP ────────────────────────────────────────────
def cleanup_expired_cache(db: Session) -> int:
    """Hapus entri cache yang sudah expired. Jalankan periodik."""
    deleted = (
        db.query(ClimateCache)
        .filter(ClimateCache.expires_at < datetime.utcnow())
        .delete(synchronize_session="fetch")
    )
    db.commit()
    logger.info(f"Cache cleanup: {deleted} entri expired dihapus")
    return deleted


def get_cache_stats(db: Session) -> dict:
    """Statistik cache untuk endpoint /model/info."""
    total   = db.query(ClimateCache).count()
    active  = db.query(ClimateCache).filter(ClimateCache.expires_at > datetime.utcnow()).count()
    expired = total - active
    return {"total": total, "active": active, "expired": expired}
