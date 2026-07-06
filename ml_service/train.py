"""
train.py
--------
Script untuk melatih model ML PanenCerdas.

Jalankan SEKALI sebelum server pertama kali:
    python train.py

Atau setelah fetch data historis dari NASA POWER:
    python scripts/fetch_historical.py
    python train.py

Mode training:
  - Tanpa argumen        : pakai data dari data/ + synthetic fallback
  - --with-db            : juga load feedback petani dari database
"""

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import argparse
from pathlib import Path
from model import train_and_save, load_models


def parse_args():
    parser = argparse.ArgumentParser(description="PanenCerdas Model Trainer")
    parser.add_argument(
        "--with-db",
        action="store_true",
        help="Load juga feedback petani dari database (butuh database sudah running)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    print("=" * 55)
    print("  🌾 PanenCerdas — Model Training Script")
    print("=" * 55)

    # Cek apakah ada data real di folder data/
    data_dir = Path(__file__).parent / "data"
    has_nasa  = (data_dir / "nasa_power_cache.csv").exists()
    has_kementan = (data_dir / "kementan_produksi.csv").exists()

    print("\n📂 Sumber data yang tersedia:")
    print(f"   NASA POWER cache  : {'✅ ada' if has_nasa else '❌ belum ada (jalankan scripts/fetch_historical.py)'}")
    print(f"   Kementan produksi CSV  : {'✅ ada' if has_kementan  else '⚠️  tidak ada (opsional, isi manual dari kementan.go.id)'}")

    db = None
    if args.with_db:
        print("\n🔌 Menghubungkan ke database untuk load feedback petani...")
        try:
            from database import init_db, SessionLocal
            init_db()
            db = SessionLocal()
            print("   ✅ Database terhubung")
        except Exception as e:
            print(f"   ⚠️  Gagal konek database: {e}")
            print("   → Lanjut training tanpa feedback petani")
            db = None

    print("\n🚀 Mulai training...\n")

    try:
        metrics = train_and_save(db=db)
    finally:
        if db:
            db.close()

    print("\n📊 Hasil Training:")
    print(f"   Data real      : {metrics.get('n_real', 0)} baris")
    print(f"   Data synthetic : {metrics.get('n_synthetic', 0)} baris")
    print(f"   MAE Harvest    : ±{metrics['mae_harvest_days']} hari")
    print(f"   MAE Yield      : ±{metrics['mae_yield']} ton/ha")
    print(f"   Risk Accuracy  : {metrics['risk_accuracy']:.1%}")

    # Registrasi versi ke DB supaya dashboard pemerintah punya angka MAE/akurasi.
    # Tanpa ini, get_latest_model_version() return None dan UI tampil "belum ada".
    print("\n💾 Registrasi versi model ke database...")
    try:
        from database import init_db, SessionLocal, save_model_version, get_latest_model_version
        init_db()
        reg_db = SessionLocal()
        try:
            latest = get_latest_model_version(reg_db)
            next_version = (latest.version + 1) if latest else 1
            save_model_version(reg_db, {
                "version":          next_version,
                "mae_harvest_days": metrics["mae_harvest_days"],
                "mae_yield":        metrics["mae_yield"],
                "risk_accuracy":    metrics["risk_accuracy"],
                "n_real":           metrics.get("n_real", 0),
                "n_synthetic":      metrics.get("n_synthetic", 0),
                "notes":            "Training manual via train.py",
            })
            print(f"   ✅ Versi {next_version} aktif di model_version")
        finally:
            reg_db.close()
    except Exception as e:
        print(f"   ⚠️  Gagal registrasi versi: {e}")
        print("   → Model .joblib tetap tersimpan, tapi dashboard akan tampil 'belum ada'")

    print("\n🔄 Memuat model yang baru dilatih...")
    ok = load_models()
    if ok:
        print("\n✅ Model siap! Jalankan server dengan:")
        print("   python main.py")
    else:
        print("\n❌ Gagal memuat model — periksa direktori saved_models/")
        sys.exit(1)
