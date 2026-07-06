# retrain_scheduler.py
"""
Retraining pipeline otomatis untuk PanenCerdas ML Service.

Cara kerja:
  1. Setiap 10 feedback baru masuk -> trigger retrain
  2. Setiap hari Minggu jam 02.00 -> scheduled retrain
  3. Retrain mendelegasikan ke model.train_and_save() — pipeline yang SAMA
     dengan train.py (data Kementan per-provinsi + feedback petani, normalisasi
     yield per-provinsi, tanpa kebocoran target). Tidak lagi melatih ulang
     dengan logika duplikat (yang dulu diam-diam mengembalikan model ke
     normalisasi nasional).
  4. Backup model lama dulu -> kalau hasil baru lebih buruk, rollback.

Selain itu: prewarm cache iklim NASA POWER terjadwal supaya peta nasional cepat.
"""

import os
import shutil
import threading
from pathlib import Path
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from database import (
    SessionLocal, get_unused_feedback, mark_feedback_used,
    get_feedback_count, save_model_version, get_latest_model_version,
)

MODEL_DIR = Path(__file__).parent / "saved_models"

# Minimum data nyata baru sebelum retrain otomatis
RETRAIN_THRESHOLD = 10

# Scheduler instance
_scheduler = BackgroundScheduler()
_current_version = 1

# Cegah retrain tumpang-tindih: dua feedback yang masuk berbarengan tidak
# boleh memicu dua retrain paralel (race di file .joblib + _current_version).
_retrain_lock = threading.Lock()


# ── CORE RETRAIN ────────────────────────────────────────
def retrain(force: bool = False, db: Session = None) -> dict:
    """
    Latih ulang model lewat model.train_and_save() — pipeline tunggal yang juga
    dipakai train.py, jadi normalisasi per-provinsi + anti-kebocoran target
    konsisten (tidak ke-revert oleh retrain).

    Backup model aktif sebelum train; rollback kalau hasil baru lebih buruk
    dibanding ModelVersion terakhir.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        counts = get_feedback_count(db)
        unused = counts["unused"]

        if not force and unused < RETRAIN_THRESHOLD:
            msg = f"Belum cukup data baru: {unused}/{RETRAIN_THRESHOLD}"
            print(f"⏭  Retrain dilewati — {msg}")
            return {"skipped": True, "reason": msg}

        from model import train_and_save, load_models

        print("\n🔄 Retraining via model.train_and_save (per-provinsi, anti-bocor)...")
        print(f"   Data nyata baru: {unused} feedback")

        old_version = get_latest_model_version(db)

        # Backup model aktif untuk rollback bila hasil baru lebih buruk.
        backup_dir = MODEL_DIR.parent / "_rollback_models"
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        if MODEL_DIR.exists():
            shutil.copytree(MODEL_DIR, backup_dir)

        # Pipeline lengkap: load real (Kementan CSV + feedback) + synthetic,
        # normalisasi per-provinsi, simpan ke saved_models/*.joblib.
        stats = train_and_save(db)
        mae_h = stats.get("mae_harvest_days", 999)
        mae_y = stats.get("mae_yield", 999)
        acc_r = stats.get("risk_accuracy", 0)
        n_real = stats.get("n_real", 0)
        n_synthetic = stats.get("n_synthetic", 0)
        print(f"   [Baru] MAE harvest: {mae_h} hari | "
              f"MAE yield: {mae_y} t/ha | Acc risk: {acc_r:.1%}")

        # Bandingkan dengan model lama (harvest MAE atau risk acc tidak memburuk).
        should_replace = True
        if old_version:
            old_mae_h = old_version.mae_harvest_days or 999
            old_acc_r = old_version.risk_accuracy or 0
            should_replace = (mae_h <= old_mae_h * 1.05) or (acc_r >= old_acc_r * 0.95)

        global _current_version

        if should_replace:
            load_models()  # aktifkan model baru di memori
            _current_version += 1
            save_model_version(db, {
                "version":          _current_version,
                "mae_harvest_days": round(mae_h, 3),
                "mae_yield":        round(mae_y, 3),
                "risk_accuracy":    round(acc_r, 4),
                "n_synthetic":      n_synthetic,
                "n_real":           n_real,
                "notes":            f"Retrain via train_and_save: {n_real} baris real, per-provinsi",
            })
            unused_rows = get_unused_feedback(db)
            mark_feedback_used(db, [r.id for r in unused_rows], _current_version)
            shutil.rmtree(backup_dir, ignore_errors=True)
            print(f"   ✅ Model v{_current_version} aktif")
            return {
                "skipped":       False,
                "replaced":      True,
                "version":       _current_version,
                "mae_harvest":   round(mae_h, 3),
                "mae_yield":     round(mae_y, 3),
                "risk_accuracy": round(acc_r, 4),
                "n_real_data":   n_real,
                "message":       f"Model v{_current_version} dilatih ({n_real} data nyata, per-provinsi)",
            }

        # ── Rollback: kembalikan model lama ──
        if backup_dir.exists():
            shutil.rmtree(MODEL_DIR, ignore_errors=True)
            shutil.copytree(backup_dir, MODEL_DIR)
            shutil.rmtree(backup_dir, ignore_errors=True)
        load_models()
        unused_rows = get_unused_feedback(db)
        mark_feedback_used(
            db, [r.id for r in unused_rows],
            old_version.version if old_version else 1,
        )
        print("   ⚠️  Model baru lebih buruk — rollback ke model lama")
        return {
            "skipped":  False,
            "replaced": False,
            "message":  "Model lama dipertahankan (rollback)",
        }

    except Exception as e:
        print(f"❌ Retrain gagal: {e}")
        return {"skipped": False, "replaced": False, "error": str(e)}
    finally:
        if close_db:
            db.close()


# ── CHECK THRESHOLD ────────────────────────────────────
def check_and_retrain_if_needed():
    """
    Dipanggil setiap kali ada feedback baru masuk (via BackgroundTasks).
    Cek apakah sudah cukup data untuk retrain — kalau ya, retrain.

    Pakai lock non-blocking: kalau ada retrain yang sedang jalan, lewati saja
    (feedback yang masuk akan ikut terhitung di retrain berikutnya).
    """
    if not _retrain_lock.acquire(blocking=False):
        print("⏭  Retrain lain sedang jalan — lewati pengecekan ini.")
        return

    db = SessionLocal()
    try:
        counts = get_feedback_count(db)
        print(f"📊 Feedback check: {counts['unused']} baru / {counts['total']} total")
        if counts["unused"] >= RETRAIN_THRESHOLD:
            print("🎯 Threshold tercapai! Memulai retrain otomatis...")
            retrain(db=db)
    finally:
        db.close()
        _retrain_lock.release()


# ── SCHEDULED RETRAIN (TIAP MINGGU) ───────────────────
def scheduled_weekly_retrain():
    """Retrain terjadwal tiap Minggu jam 02.00 — dipaksa meski belum threshold."""
    print(f"\n⏰ Scheduled weekly retrain — {datetime.now()}")
    with _retrain_lock:
        retrain(force=True)


# ── SCHEDULER SETUP ────────────────────────────────────
def start_scheduler():
    """Mulai scheduler background. Dipanggil saat FastAPI startup."""
    if _scheduler.running:
        return

    # Job terjadwal: tiap Minggu jam 02.00
    _scheduler.add_job(
        scheduled_weekly_retrain,
        trigger=CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="weekly_retrain",
        replace_existing=True,
    )

    # Prewarm cache iklim berkala supaya peta nasional (province=ALL) selalu cepat.
    # Default tiap 330 menit (5j30m) < TTL cache 6 jam → cache tak pernah basi.
    # Jalan sekali langsung saat boot juga (next_run_time=now).
    if os.getenv("PREWARM_CLIMATE", "true").strip().lower() in ("1", "true", "yes", "on"):
        try:
            interval_min = int(os.getenv("PREWARM_CLIMATE_INTERVAL_MIN", "330"))
        except ValueError:
            interval_min = 330
        from prewarm import prewarm_climate
        _scheduler.add_job(
            prewarm_climate,
            trigger=IntervalTrigger(minutes=interval_min),
            id="prewarm_climate",
            replace_existing=True,
            next_run_time=datetime.now(),  # hangatkan langsung saat startup
            max_instances=1,
            coalesce=True,
        )
        print(f"🔥 Prewarm iklim aktif (tiap {interval_min} menit + sekali saat boot)")

    _scheduler.start()
    print("⏰ Retrain scheduler aktif (tiap Minggu 02.00 WIB)")


def stop_scheduler():
    """Hentikan scheduler saat FastAPI shutdown."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("⏰ Scheduler dihentikan")
