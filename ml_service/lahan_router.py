"""
lahan_router.py
---------------
Endpoint daftar lahan petani.

Tidak ada tabel `Lahan` di DB — kita derive dari prediction_log:
satu lahan = satu kombinasi (petani_id, lahan_id) yang pernah diprediksi.
Status & catatan dihitung dari prediksi terakhir + feedback yang sudah masuk.

Kalau di kemudian hari ada registrasi lahan eksplisit, ganti query
sumber ke tabel Lahan tanpa ubah response shape.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import get_db, PredictionLog, TrainingFeedback

router = APIRouter(prefix="/api/lahan", tags=["lahan"])


def _not_archived():
    """Predikat baris lahan yang masih aktif (belum diarsipkan).

    Pakai OR is_(None) supaya baris pra-migrasi (kolom NULL) tetap dianggap aktif.
    """
    return or_(
        PredictionLog.lahan_archived == False,  # noqa: E712
        PredictionLog.lahan_archived.is_(None),
    )


class LahanUpdate(BaseModel):
    """Field yang bisa diubah pada satu lahan.

    Lahan tidak punya tabel sendiri — ia derive dari prediction_log. Karena itu:
      - `new_lahan_id` me-rename SEMUA baris prediksi + feedback lahan ini.
      - `land_area_ha` hanya menimpa luas pada prediksi TERBARU (yang jadi
        sumber angka "Luas" di kartu lahan); riwayat input lama dibiarkan utuh
        supaya tidak memalsukan data yang sudah dipakai model.
    """

    new_lahan_id: Optional[str] = None
    land_area_ha: Optional[float] = None


def _status_from_pred(crop_type: Optional[str], risk: Optional[str]) -> str:
    if not crop_type:
        return "kosong"
    if risk == "high":
        return "panen-segera"
    return "tumbuh"


@router.get("", summary="Daftar lahan petani")
def list_lahan(
    petani_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Daftar lahan yang pernah diprediksi.

    - Tanpa `petani_id` -> semua lahan terdaftar (admin/debug)
    - Dengan `petani_id` -> hanya lahan milik petani itu
    """
    query = db.query(PredictionLog)
    if petani_id:
        query = query.filter(PredictionLog.petani_id == petani_id)

    rows = query.filter(
        PredictionLog.lahan_id.isnot(None),
        _not_archived(),
    ).all()

    grouped: dict[tuple[str, str], list[PredictionLog]] = {}
    for r in rows:
        key = (r.petani_id or "_", r.lahan_id)
        grouped.setdefault(key, []).append(r)

    items = []
    for (pid, lid), preds in grouped.items():
        preds.sort(key=lambda p: p.created_at, reverse=True)
        last = preds[0]

        # Koordinat lahan: ambil dari prediksi terbaru yang punya lat+lon
        # (prediksi default/manual tidak punya GPS, jadi mungkin perlu mundur).
        last_with_coords = next(
            (p for p in preds if p.lat is not None and p.lon is not None),
            None,
        )

        fb_count = (
            db.query(func.count(TrainingFeedback.id))
            .filter(TrainingFeedback.lahan_id == lid)
            .scalar()
        ) or 0

        items.append({
            "lahan_id":               lid,
            "petani_id":              pid if pid != "_" else None,
            "last_crop_type":         last.crop_type,
            "last_yield_ton_per_ha":  last.pred_yield_ton_per_ha,
            "last_harvest_days":      last.pred_harvest_days,
            "last_risk_level":        last.pred_risk_level,
            "last_land_area_ha":      last.land_area_ha,
            "last_lat":               last_with_coords.lat if last_with_coords else None,
            "last_lon":               last_with_coords.lon if last_with_coords else None,
            "last_predicted_at":      last.created_at.isoformat(),
            "total_predictions":      len(preds),
            "total_feedback":         fb_count,
            "status":                 _status_from_pred(last.crop_type, last.pred_risk_level),
        })

    items.sort(key=lambda x: x["last_predicted_at"], reverse=True)

    total_ha = round(sum(i["last_land_area_ha"] or 0 for i in items), 2)
    aktif = sum(1 for i in items if i["status"] != "kosong")

    return {
        "petani_id":  petani_id,
        "total":      len(items),
        "total_ha":   total_ha,
        "aktif":      aktif,
        "items":      items,
    }


def _lahan_query(db: Session, lahan_id: str, petani_id: Optional[str]):
    """Query baris prediction_log AKTIF untuk satu lahan (opsional per petani).

    Hanya menyentuh baris non-arsip — endpoint edit/hapus bekerja pada lahan yang
    sedang tampil di daftar.
    """
    q = db.query(PredictionLog).filter(
        PredictionLog.lahan_id == lahan_id,
        _not_archived(),
    )
    if petani_id:
        q = q.filter(PredictionLog.petani_id == petani_id)
    return q


@router.patch("/{lahan_id}", summary="Edit nama / luas lahan")
def update_lahan(
    lahan_id: str,
    payload: LahanUpdate,
    petani_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Rename lahan dan/atau ubah luas lahan terbaru.

    Body:
      - `new_lahan_id` -> nama baru (<= 50 char). Diterapkan ke semua prediksi
        + feedback lahan ini.
      - `land_area_ha` -> luas baru (> 0), ditimpa ke prediksi terbaru.
    """
    preds = _lahan_query(db, lahan_id, petani_id) \
        .order_by(PredictionLog.created_at.desc()).all()
    if not preds:
        raise HTTPException(status_code=404, detail=f"Lahan '{lahan_id}' tidak ditemukan")

    if payload.land_area_ha is not None:
        if payload.land_area_ha <= 0:
            raise HTTPException(status_code=400, detail="Luas lahan harus lebih dari 0")
        preds[0].land_area_ha = payload.land_area_ha

    final_id = lahan_id
    new_name = (payload.new_lahan_id or "").strip()
    if new_name and new_name != lahan_id:
        if len(new_name) > 50:
            raise HTTPException(status_code=400, detail="Nama lahan maksimal 50 karakter")
        for p in preds:
            p.lahan_id = new_name
        fb_q = db.query(TrainingFeedback).filter(TrainingFeedback.lahan_id == lahan_id)
        if petani_id:
            fb_q = fb_q.filter(TrainingFeedback.petani_id == petani_id)
        fb_q.update({TrainingFeedback.lahan_id: new_name}, synchronize_session=False)
        final_id = new_name

    db.commit()
    return {
        "updated":             True,
        "lahan_id":            final_id,
        "previous_lahan_id":   lahan_id,
        "predictions_updated": len(preds),
    }


@router.delete("/{lahan_id}", summary="Arsipkan lahan (riwayat & feedback tetap disimpan)")
def delete_lahan(
    lahan_id: str,
    petani_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Arsipkan lahan — bukan hard delete.

    Lahan hilang dari daftar /api/lahan, tapi baris prediction_log + seluruh
    training_feedback DIBIARKAN UTUH supaya riwayat prediksi petani dan data
    ground-truth untuk melatih model tidak ikut terhapus.
    """
    pred_q = _lahan_query(db, lahan_id, petani_id)
    n_pred = pred_q.count()
    if n_pred == 0:
        raise HTTPException(status_code=404, detail=f"Lahan '{lahan_id}' tidak ditemukan")

    pred_q.update(
        {PredictionLog.lahan_archived: True},
        synchronize_session=False,
    )
    db.commit()

    return {
        "archived":             True,
        "lahan_id":             lahan_id,
        "predictions_archived": n_pred,
    }
