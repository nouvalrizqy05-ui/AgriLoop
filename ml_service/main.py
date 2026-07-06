"""
main.py
-------
PanenCerdas ML Service - entry point.

Sumber: V2 (Muhammad Choirudin Ammar) + integrasi router pemerintah dari main.
Perubahan vs V2:
  - Path /predict, /feedback, /health di-prefix /api supaya Express gateway
    bisa proxy 1:1 tanpa perlu rewrite path.
  - Field iklim sekarang opsional; kalau None dan tidak ada lat/lon -> diisi
    Indonesia defaults sebelum prediksi.
  - Mount dashboard_router, predictions_router, regions_router untuk sisi
    pemerintah (frontend /pemerintah/* memanggil endpoint ini lewat Express).
"""

import sys
# V2 banyak pakai emoji di print(); paksa stdout UTF-8 supaya Windows cp1252
# console tidak crash dengan UnicodeEncodeError saat boot.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_
from sqlalchemy.orm import Session
import uvicorn

from schemas import PredictInput, PredictOutput, HealthResponse
from model import load_models, is_model_loaded, predict as ml_predict
from database import (
    init_db, get_db, save_prediction_log,
    get_feedback_count, get_latest_model_version,
    PredictionLog,
)
from data_cache import init_cache, get_or_fetch_climate, get_cache_stats, cleanup_expired_cache
from data_fetcher import INDONESIA_DEFAULTS, fetch_climate_daily, fetch_forecast_daily
from ndvi_fetcher import get_or_fetch_ndvi
from feedback_router import router as feedback_router
from dashboard_router import router as dashboard_router
from predictions_router import router as predictions_router
from regions_router import router as regions_router
from lahan_router import router as lahan_router
from retrain_scheduler import start_scheduler, stop_scheduler, retrain
from prewarm import start_background_prewarm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


DEFAULT_NDVI = 0.6


# ── LIFESPAN ───────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\nPanenCerdas ML Service starting...")
    init_db()
    init_cache()
    loaded = load_models()
    if not loaded:
        print("Model belum ada - jalankan: python train.py (fallback rules aktif)")
    else:
        # Warm sklearn/pandas: panggilan .predict() pertama kena overhead JIT/
        # validasi (~beberapa detik). Bayar sekali saat boot supaya request peta
        # nasional pertama tidak lambat.
        try:
            from model import predict_yield_batch
            from schemas import PredictInput
            from database import SessionLocal
            from data_cache import get_cached_climate_batch

            predict_yield_batch([(
                PredictInput(
                    crop_type="padi", land_area_ha=1000.0, rainfall_mm=120.0,
                    temperature_c=27.0, solar_radiation=185.0, ndvi=0.65,
                    pest_pressure=0.0, variety="Lokal",
                ), None,
            )])
            # Query ORM pertama di proses kena overhead kompilasi SQLAlchemy
            # (~4s). Pancing di sini supaya request peta nasional pertama cepat.
            _wdb = SessionLocal()
            try:
                get_cached_climate_batch(_wdb, [(-2.5, 117.5)], period_days=30)
            finally:
                _wdb.close()
            print("Model + cache warmup selesai")
        except Exception as e:
            print(f"Warmup dilewati: {e}")
    start_scheduler()
    prewarm_task = start_background_prewarm()
    print("ML Service ready at http://localhost:8000\n")
    yield
    stop_scheduler()
    if prewarm_task is not None and not prewarm_task.done():
        prewarm_task.cancel()
    print("ML Service shutdown")


# ── APP ────────────────────────────────────────────────
app = FastAPI(
    title="PanenCerdas ML Service",
    version="2.3.0",
    description=(
        "Prediksi panen dengan ML + data iklim NASA POWER + NDVI MODIS APPEEARS. "
        "Endpoint /api/predict + /api/feedback dipakai aplikasi petani; "
        "endpoint /api/dashboard, /api/predictions, /api/regions dipakai dashboard pemerintah."
    ),
    lifespan=lifespan,
)

# CORS: comma-separated list di env ALLOWED_ORIGINS. Default "*" untuk dev;
# prod harus diisi domain Express gateway (+ frontend kalau memang akses
# langsung). Contoh: "https://api.panen.app,https://panen.app".
_origins_env = os.getenv("ALLOWED_ORIGINS", "*").strip()
allowed_origins = (
    ["*"] if _origins_env == "*"
    else [o.strip() for o in _origins_env.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(feedback_router)         # /api/feedback (prefix di router)
app.include_router(dashboard_router)        # /api/dashboard/*
app.include_router(predictions_router)      # /api/predictions[/{id}]
app.include_router(regions_router)          # /api/regions[/geojson]
app.include_router(lahan_router)            # /api/lahan


# ── ROUTES ─────────────────────────────────────────────
@app.get("/", tags=["info"])
def root():
    return {
        "service": "PanenCerdas ML Service",
        "version": "2.3.0",
        "docs":    "/docs",
        "features": [
            "ml_prediction",
            "real_climate_data_nasa_power",
            "real_ndvi_modis_appeears",
            "online_learning",
            "auto_retrain",
            "pemerintah_dashboard_routes",
        ],
    }


@app.get("/api/health", response_model=HealthResponse, tags=["info"])
def health(db: Session = Depends(get_db)):
    ver = get_latest_model_version(db)
    fb  = get_feedback_count(db)
    cs  = get_cache_stats(db)
    return HealthResponse(
        status="ok",
        model_loaded=is_model_loaded(),
        service="PanenCerdas ML Service",
        version=f"v{ver.version}" if ver else "v1 (synthetic only)",
        feedback_stats=fb,
        cache_stats=cs,
    )


def _fill_climate_defaults(data: PredictInput) -> None:
    """Isi field iklim yang None dengan Indonesia defaults supaya model/fallback
    rules tidak crash. Mutates data in-place (PredictInput.frozen=False)."""
    if data.temperature_c is None:
        data.temperature_c = INDONESIA_DEFAULTS["temperature_c"]
    if data.rainfall_mm is None:
        data.rainfall_mm = INDONESIA_DEFAULTS["rainfall_mm"]
    if data.solar_radiation is None:
        data.solar_radiation = INDONESIA_DEFAULTS["solar_radiation"]
    if data.ndvi is None:
        data.ndvi = DEFAULT_NDVI


@app.post("/api/predict", response_model=PredictOutput, tags=["prediction"])
async def predict_harvest(
    data: PredictInput,
    petani_id: str = None,
    lahan_id: str = None,
    db: Session = Depends(get_db),
):
    """
    Prediksi panen.

    - lat+lon ada -> iklim dari NASA POWER, NDVI dari APPEEARS (override input).
    - lat/lon kosong -> pakai field iklim yang dikirim; sisanya default Indonesia.
    """
    climate_source = "user_input"
    ndvi_source    = "user_input"

    try:
        if data.lat is not None and data.lon is not None:
            import asyncio
            climate_task = get_or_fetch_climate(
                lat=data.lat, lon=data.lon, db=db, period_days=30,
            )
            ndvi_task = get_or_fetch_ndvi(
                lat=data.lat, lon=data.lon, db=db,
                crop_type=data.crop_type, days_back=32,
            )
            climate, ndvi_result = await asyncio.gather(climate_task, ndvi_task)

            data.temperature_c   = climate["temperature_c"]
            data.rainfall_mm     = climate["rainfall_mm"]
            data.solar_radiation = climate["solar_radiation"]
            climate_source       = climate.get("data_source", "nasa_power")

            data.ndvi  = ndvi_result["ndvi"]
            ndvi_source = ndvi_result["ndvi_source"]

            logger.info(
                f"Overrides: iklim={climate_source} | NDVI={data.ndvi} [{ndvi_source}]"
            )

        _fill_climate_defaults(data)

        # Satu lahan = satu komoditas. Kalau lahan ini masih punya tanaman aktif
        # (prediksi belum diarsip / belum dipanen) dengan komoditas berbeda, tolak
        # — petani harus lapor panen dulu (yang menutup lahan) atau pakai nama lain.
        if lahan_id:
            active_q = db.query(PredictionLog).filter(
                PredictionLog.lahan_id == lahan_id,
                or_(
                    PredictionLog.lahan_archived == False,  # noqa: E712
                    PredictionLog.lahan_archived.is_(None),
                ),
                PredictionLog.crop_type != data.crop_type,
            )
            if petani_id:
                active_q = active_q.filter(PredictionLog.petani_id == petani_id)
            conflict = active_q.first()
            if conflict is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Lahan '{lahan_id}' sedang menanam {conflict.crop_type}. "
                        f"Satu lahan hanya untuk satu komoditas — laporkan panennya "
                        f"dulu, atau pakai nama lahan lain untuk {data.crop_type}."
                    ),
                )

        result = ml_predict(data)

        log = save_prediction_log(
            db,
            input_data={
                **data.model_dump(),
                "petani_id": petani_id,
                "lahan_id":  lahan_id,
            },
            output_data=result.model_dump(),
        )

        result_dict = result.model_dump()
        result_dict["prediction_log_id"] = log.id
        result_dict["climate_source"]    = climate_source
        result_dict["ndvi_source"]       = ndvi_source
        return result_dict

    except HTTPException:
        # Error tervalidasi (mis. 409 konflik komoditas) diteruskan apa adanya,
        # jangan dibungkus jadi 500.
        raise
    except Exception as e:
        logger.error(f"Predict error: {e}")
        raise HTTPException(status_code=500, detail=f"Prediction error: {str(e)}")


# ── WEATHER (untuk halaman /petani/cuaca) ──────────────
_HARI_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_BULAN_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
             "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def _classify_cuaca(rain_mm: float, radiation: float | None) -> str:
    if rain_mm >= 15:
        return "hujan-lebat"
    if rain_mm >= 2:
        return "hujan-ringan"
    if radiation is not None and radiation < 180:
        return "berawan"
    return "cerah"


def _cuaca_from_code(code: int | None) -> str | None:
    """Map kode cuaca WMO (Open-Meteo) -> label UI. None kalau kode tak dikenal."""
    if code is None:
        return None
    if code in (0, 1):
        return "cerah"
    if code in (2, 3, 45, 48):
        return "berawan"
    if code in (65, 66, 67, 82, 95, 96, 99):
        return "hujan-lebat"
    if code in (51, 53, 55, 56, 57, 61, 63, 80, 81):
        return "hujan-ringan"
    return "berawan"


def _catatan_for(cuaca: str, rain_mm: float, t_max: float | None) -> str:
    if cuaca == "hujan-lebat":
        return "Periksa drainase - potensi genangan di petak rendah."
    if cuaca == "hujan-ringan":
        return "Tunda pemupukan dan penyemprotan - hujan ringan dapat melarutkan."
    if cuaca == "berawan":
        return "Awan tebal - fotosintesis berkurang, jaga kelembapan tanah."
    if t_max is not None and t_max >= 32:
        return "Radiasi tinggi - pastikan irigasi cukup, semprot pagi/sore."
    return "Hari baik untuk pemupukan dan penyemprotan hama pagi."


@app.get("/api/weather/recent", tags=["weather"])
async def weather_recent(lat: float = -7.855, lon: float = 110.42, days: int = 7):
    """
    PRAKIRAAN cuaca harian KE DEPAN (Open-Meteo) untuk koordinat tertentu.
    Default centroid DI Yogyakarta. Dipakai dashboard + halaman /petani/cuaca.

    Open-Meteo = layanan forecast sungguhan (ramalan ke depan). Kalau gagal,
    fallback ke NASA POWER (historis, ringkasan beberapa hari terakhir) supaya
    halaman tetap ada isinya — ditandai lewat field `source`.
    """
    from datetime import date as _date

    days = max(1, min(days, 14))  # clamp 1..14

    # Konversi solar (MJ/m^2/hari) -> W/m^2 untuk label UI.
    # 1 MJ/m^2/hari = 1e6 J / 86400 s = ~11.574 W/m^2 (rata-rata harian).
    MJ_TO_W = 11.574

    # 1) Forecast ke depan (Open-Meteo)
    series = await fetch_forecast_daily(lat=lat, lon=lon, days=days)
    source = "open_meteo_forecast"

    # 2) Fallback: ringkasan NASA POWER (historis) kalau forecast gagal.
    if not series:
        nasa = await fetch_climate_daily(lat=lat, lon=lon, days_back=days + 7)
        series = nasa[-days:]
        source = "nasa_power_recent"

    items = []
    for row in series:
        d = _date.fromisoformat(row["date"])
        hari = _HARI_ID[d.weekday()]
        tanggal = f"{d.day:02d} {_BULAN_ID[d.month - 1]}"
        rad_mj = row.get("solar_radiation")
        rad_w  = round(rad_mj * MJ_TO_W) if rad_mj is not None else None
        rain   = row.get("rainfall_mm") or 0.0
        t_min  = row.get("temperature_min")
        t_max  = row.get("temperature_max")
        t_mean = row.get("temperature_mean")
        # Forecast bawa weather_code WMO (lebih akurat); kalau tak ada, klasifikasi
        # dari hujan + radiasi.
        cuaca   = _cuaca_from_code(row.get("weather_code")) or _classify_cuaca(rain, rad_w)
        catatan = _catatan_for(cuaca, rain, t_max)
        items.append({
            "date":         row["date"],
            "hari":         hari,
            "tanggal":      tanggal,
            "cuaca":        cuaca,
            "suhu_min":     t_min if t_min is not None else t_mean,
            "suhu_max":     t_max if t_max is not None else t_mean,
            "suhu_mean":    t_mean,
            "hujan_mm":     rain,
            "radiasi_w_m2": rad_w,
            "catatan":      catatan,
        })

    return {
        "lat":    lat,
        "lon":    lon,
        "source": source if items else "unavailable",
        "items":  items,
    }


@app.post("/api/retrain", tags=["admin"])
def trigger_retrain(force: bool = False, db: Session = Depends(get_db)):
    return retrain(force=force, db=db)


@app.get("/api/model/info", tags=["admin"])
def model_info(db: Session = Depends(get_db)):
    ver = get_latest_model_version(db)
    fb  = get_feedback_count(db)
    cs  = get_cache_stats(db)
    unused    = fb.get("unused", 0)
    threshold = 10

    return {
        "model_loaded":   is_model_loaded(),
        "active_version": ver.version if ver else None,
        "trained_at":     ver.trained_at.isoformat() if ver else None,
        "metrics": {
            "mae_harvest_days": ver.mae_harvest_days if ver else None,
            "mae_yield":        ver.mae_yield        if ver else None,
            "risk_accuracy":    ver.risk_accuracy    if ver else None,
        },
        "training_data": {
            "n_synthetic": ver.n_synthetic if ver else None,
            "n_real":      ver.n_real      if ver else None,
        },
        "feedback_pool": fb,
        "climate_cache": cs,
        "next_retrain":  f"Perlu {max(0, threshold - unused)} feedback lagi untuk auto-retrain",
        "data_sources": {
            "climate": "NASA POWER (suhu, hujan, radiasi) - cache 6 jam",
            "ndvi":    "NASA APPEEARS MODIS MOD13Q1 - cache 24 jam, fallback ke estimasi musiman",
        },
    }


@app.delete("/api/cache/expired", tags=["admin"])
def clear_expired_cache(db: Session = Depends(get_db)):
    deleted = cleanup_expired_cache(db)
    return {"deleted": deleted, "message": f"{deleted} entri cache expired dihapus"}


# ── VARIETIES (untuk dropdown form prediksi) ───────────
@app.get("/api/varieties", tags=["catalog"])
def list_varieties(crop_type: str | None = None):
    """
    Daftar varietas per komoditas dari VARIETY_CATALOG model.

    - Tanpa parameter -> return semua komoditas
    - Dengan ?crop_type=padi -> return varietas untuk komoditas itu saja
    """
    from model import VARIETY_CATALOG, BASE_YIELD, BASE_HARVEST

    def _format(crop: str, vlist: list[tuple[str, float, int]]) -> list[dict]:
        base_yield = BASE_YIELD.get(crop, 5.0)
        base_days  = BASE_HARVEST.get(crop, 100)
        return [
            {
                "name":            name,
                "yield_modifier":  yield_mod,
                "days_modifier":   days_mod,
                "estimated_yield": round(base_yield * yield_mod, 2),
                "estimated_days":  base_days + days_mod,
            }
            for name, yield_mod, days_mod in vlist
        ]

    if crop_type:
        if crop_type not in VARIETY_CATALOG:
            raise HTTPException(
                status_code=404,
                detail=f"Komoditas '{crop_type}' tidak dikenal",
            )
        return {
            "crop_type": crop_type,
            "varieties": _format(crop_type, VARIETY_CATALOG[crop_type]),
        }

    return {
        crop: _format(crop, vlist)
        for crop, vlist in VARIETY_CATALOG.items()
    }


if __name__ == "__main__":
    # PaaS (Render/Railway/Fly) assigns PORT; lokal fallback ke 8000.
    # UVICORN_RELOAD=true buat hot-reload saat dev; default off di prod.
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("UVICORN_RELOAD", "false").lower() in ("1", "true", "yes")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload)
