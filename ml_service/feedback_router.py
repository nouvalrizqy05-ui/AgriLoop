"""
feedback_router.py
------------------
Router untuk feedback petani — lapor hasil panen NYATA setelah panen selesai.

Adaptasi vs V2:
  - Path: /api/feedback (V2 sebelumnya /feedback/) supaya konsisten dengan
    skema /api/* dan Express gateway.
  - Field iklim & lahan jadi OPSIONAL. Kalau prediction_log_id dikirim, kita
    auto-load dari tabel PredictionLog (frontend tidak perlu kirim ulang).
  - actual_risk_level otomatis diturunkan dari yield ratio kalau tidak dikirim.
  - Field `notes` (frontend) dipetakan ke `catatan` (V2 column).
  - Response shape: tambah `status: "received"` supaya cocok dengan
    type frontend FeedbackResponse, sambil tetap kirim field bonus V2.
"""

from typing import Optional, Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import (
    get_db, save_feedback,
    get_feedback_count, get_prediction_stats,
    PredictionLog, TrainingFeedback,
)
from retrain_scheduler import check_and_retrain_if_needed, RETRAIN_THRESHOLD


# Base yields untuk inferensi risk level — harus sinkron dengan model.BASE_YIELD
_BASE_YIELDS = {
    "padi":         5.2,
    "jagung":       5.8,
    "kedelai":      1.5,
    "ubi_kayu":     20.0,
    "ubi_jalar":    15.0,
    "cabe_besar":   8.0,
    "cabe_rawit":   6.0,
    "bawang_merah": 9.5,
    "bawang_putih": 7.0,
}


def _infer_risk(crop_type: str, actual_yield: float) -> str:
    base = _BASE_YIELDS.get(crop_type, 5.0)
    ratio = actual_yield / base if base > 0 else 1.0
    if ratio >= 0.85:
        return "low"
    if ratio >= 0.65:
        return "medium"
    return "high"


router = APIRouter(prefix="/api/feedback", tags=["feedback"])


# ── SCHEMAS ────────────────────────────────────────────
class FeedbackInput(BaseModel):
    """Frontend wajib kirim prediction_log_id + actual_*; sisanya auto-load."""

    prediction_log_id: Optional[int] = Field(
        None,
        description="ID prediksi sebelumnya. Kalau ada → field lain auto-load dari log.",
    )

    actual_harvest_days: int = Field(..., gt=0, le=400)
    actual_yield_ton_per_ha: float = Field(..., gt=0)

    # Frontend kirim `notes`; V2 column-nya `catatan`. Terima dua-duanya.
    notes: Optional[str] = None
    catatan: Optional[str] = None

    # Override opsional — kalau prediction_log_id tidak ada, semua wajib
    ndvi: Optional[float] = Field(None, ge=0.0, le=1.0)
    rainfall_mm: Optional[float] = Field(None, ge=0.0)
    temperature_c: Optional[float] = Field(None, ge=10.0, le=50.0)
    solar_radiation: Optional[float] = Field(None, ge=0.0)
    land_area_ha: Optional[float] = Field(None, gt=0.0)

    # 9 komoditas lengkap — sinkron dengan schemas.CropType & model.CROP_TYPES
    crop_type: Optional[Literal[
        "padi",
        "jagung",
        "kedelai",
        "ubi_kayu",
        "ubi_jalar",
        "cabe_besar",
        "cabe_rawit",
        "bawang_merah",
        "bawang_putih",
    ]] = None

    actual_risk_level: Optional[Literal["low", "medium", "high"]] = None

    # Fitur tambahan — opsional, dipakai model v2.4+
    pest_pressure: Optional[float] = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Tekanan hama saat tanam (0.0–1.0). Default 0.0.",
    )
    variety: Optional[str] = Field(
        default="Lokal",
        description="Varietas yang ditanam. Default 'Lokal'.",
    )

    petani_id: Optional[str] = None
    lahan_id: Optional[str] = None


class FeedbackResponse(BaseModel):
    # `status` ditambah supaya match dengan TS type frontend
    status: Literal["received"] = "received"
    feedback_id: int
    success: bool = True
    message: str = ""
    total_feedback_terkumpul: int = 0
    estimasi_retrain: str = ""


class FeedbackStats(BaseModel):
    feedback: dict
    predictions: dict
    pesan: str


# ── ENDPOINTS ──────────────────────────────────────────
@router.post("", response_model=FeedbackResponse, summary="Lapor hasil panen nyata")
def submit_feedback(
    data: FeedbackInput,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        # Auto-load dari prediction_log kalau ID dikirim
        log: Optional[PredictionLog] = None
        if data.prediction_log_id is not None:
            log = (
                db.query(PredictionLog)
                .filter(PredictionLog.id == data.prediction_log_id)
                .first()
            )

        def pick(field: str, default=None):
            val = getattr(data, field, None)
            if val is not None:
                return val
            if log is not None:
                return getattr(log, field, default)
            return default

        ndvi            = pick("ndvi")
        rainfall_mm     = pick("rainfall_mm")
        temperature_c   = pick("temperature_c")
        solar_radiation = pick("solar_radiation")
        land_area_ha    = pick("land_area_ha")
        crop_type       = pick("crop_type")

        missing = [
            name for name, val in {
                "ndvi": ndvi,
                "rainfall_mm": rainfall_mm,
                "temperature_c": temperature_c,
                "solar_radiation": solar_radiation,
                "land_area_ha": land_area_ha,
                "crop_type": crop_type,
            }.items()
            if val is None
        ]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Field wajib tidak ada: {missing}. "
                    "Kirim prediction_log_id (lookup dari /api/predict) atau "
                    "isi field ini secara eksplisit."
                ),
            )

        actual_risk_level = data.actual_risk_level or _infer_risk(
            crop_type, data.actual_yield_ton_per_ha,
        )

        fb = save_feedback(db, {
            "prediction_log_id":       data.prediction_log_id,
            "ndvi":                    ndvi,
            "rainfall_mm":             rainfall_mm,
            "temperature_c":           temperature_c,
            "solar_radiation":         solar_radiation,
            "land_area_ha":            land_area_ha,
            "crop_type":               crop_type,
            "actual_harvest_days":     data.actual_harvest_days,
            "actual_yield_ton_per_ha": data.actual_yield_ton_per_ha,
            "actual_risk_level":       actual_risk_level,
            "petani_id":               data.petani_id,
            "lahan_id":                data.lahan_id,
            "catatan":                 data.catatan or data.notes,
            "pest_pressure":           data.pest_pressure or 0.0,
            "variety":                 data.variety or "Lokal",
        })

        if log is not None:
            log.feedback_given = True
            # Panen sudah dilaporkan -> siklus lahan ini selesai. Arsipkan lahan
            # (hilang dari daftar lahan aktif), tapi prediksi + feedback tetap
            # tersimpan sebagai riwayat & data latih. Satu lahan = satu komoditas,
            # jadi feedback panen menutup seluruh lahan, bukan satu prediksi saja.
            if log.lahan_id:
                arch_q = db.query(PredictionLog).filter(
                    PredictionLog.lahan_id == log.lahan_id
                )
                if log.petani_id is not None:
                    arch_q = arch_q.filter(PredictionLog.petani_id == log.petani_id)
                else:
                    arch_q = arch_q.filter(PredictionLog.petani_id.is_(None))
                arch_q.update(
                    {PredictionLog.lahan_archived: True},
                    synchronize_session=False,
                )
            db.commit()

        counts = get_feedback_count(db)
        total  = counts["total"]
        remaining = RETRAIN_THRESHOLD - (counts["unused"] % RETRAIN_THRESHOLD)
        if counts["unused"] >= RETRAIN_THRESHOLD:
            # Ambang tercapai → jalankan retrain di background (non-blocking)
            # supaya respons feedback tetap cepat. Lock di scheduler mencegah
            # retrain tumpang-tindih kalau banyak feedback masuk berbarengan.
            bg.add_task(check_and_retrain_if_needed)
            estimasi = "Threshold tercapai - retrain otomatis sedang berjalan"
        else:
            estimasi = f"Perlu {remaining} feedback lagi untuk trigger retrain"

        return FeedbackResponse(
            status="received",
            feedback_id=fb.id,
            success=True,
            message="Data panen tersimpan",
            total_feedback_terkumpul=total,
            estimasi_retrain=estimasi,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal menyimpan feedback: {str(e)}",
        )


@router.get("/stats", response_model=FeedbackStats, summary="Statistik feedback")
def get_stats(db: Session = Depends(get_db)):
    feedback_stats   = get_feedback_count(db)
    prediction_stats = get_prediction_stats(db)

    total = feedback_stats["total"]
    if total < 10:
        pesan = f"Terkumpul {total}/10 feedback - belum cukup untuk retrain"
    elif total < 50:
        pesan = f"Terkumpul {total} feedback - model mulai belajar dari data nyata"
    else:
        pesan = f"Terkumpul {total} feedback - model belajar optimal dari data petani"

    return FeedbackStats(
        feedback=feedback_stats,
        predictions=prediction_stats,
        pesan=pesan,
    )


@router.get("/history", summary="Riwayat feedback")
def get_history(
    petani_id: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    query = db.query(TrainingFeedback)
    if petani_id:
        query = query.filter(TrainingFeedback.petani_id == petani_id)
    rows = query.order_by(TrainingFeedback.created_at.desc()).limit(limit).all()

    return {
        "total": len(rows),
        "data": [
            {
                "id":                    r.id,
                "crop_type":             r.crop_type,
                "actual_harvest_days":   r.actual_harvest_days,
                "actual_yield_ton_per_ha": r.actual_yield_ton_per_ha,
                "actual_risk_level":     r.actual_risk_level,
                "catatan":               r.catatan,
                "used_in_training":      r.used_in_training,
                "created_at":            r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
