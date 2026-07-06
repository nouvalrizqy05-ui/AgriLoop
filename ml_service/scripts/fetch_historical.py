"""
scripts/fetch_historical.py
---------------------------
Script sekali jalan untuk seed data iklim historis dari NASA POWER.
Jalankan SEBELUM train.py agar model pertama sudah punya data real.

Cara pakai:
  python scripts/fetch_historical.py

Output:
  data/nasa_power_cache.csv   ← data iklim per lokasi sample
  data/kementan_template.csv       ← template CSV untuk isi data Kementan manual

Estimasi waktu: ~2–5 menit (tergantung koneksi internet)
"""

import asyncio
import csv
import sys
from pathlib import Path

# Tambah parent directory ke path agar bisa import modul ml-service
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from data_fetcher import fetch_bulk_for_training, estimate_ndvi_from_season

# ── LOKASI SAMPLE ──────────────────────────────────────
# Representasi sentra produksi pertanian utama Indonesia
# Sumber koordinat: Google Maps / Wikipedia
SAMPLE_LOCATIONS = [
    # Jawa (sentra padi & jagung terbesar)
    {"lat": -7.25,  "lon": 112.75, "provinsi": "Jawa Timur",   "crop_type": "padi",    "land_area_ha": 1.5},
    {"lat": -7.80,  "lon": 110.36, "provinsi": "Jawa Tengah",  "crop_type": "padi",    "land_area_ha": 1.2},
    {"lat": -6.90,  "lon": 107.60, "provinsi": "Jawa Barat",   "crop_type": "padi",    "land_area_ha": 1.8},
    {"lat": -7.25,  "lon": 112.75, "provinsi": "Jawa Timur",   "crop_type": "jagung",  "land_area_ha": 2.0},
    {"lat": -7.80,  "lon": 110.36, "provinsi": "Jawa Tengah",  "crop_type": "kedelai", "land_area_ha": 0.8},
    {"lat": -6.20,  "lon": 106.82, "provinsi": "DKI Jakarta",  "crop_type": "padi",    "land_area_ha": 0.5},
    {"lat": -8.65,  "lon": 115.22, "provinsi": "Bali",          "crop_type": "padi",    "land_area_ha": 0.9},
    # Sumatera
    {"lat":  3.60,  "lon":  98.67, "provinsi": "Sumatera Utara", "crop_type": "padi",   "land_area_ha": 2.0},
    {"lat": -0.95,  "lon": 100.36, "provinsi": "Sumatera Barat", "crop_type": "padi",   "land_area_ha": 1.5},
    {"lat":  3.58,  "lon":  98.68, "provinsi": "Sumatera Utara", "crop_type": "jagung", "land_area_ha": 1.8},
    {"lat": -4.90,  "lon": 105.27, "provinsi": "Lampung",        "crop_type": "jagung", "land_area_ha": 3.0},
    {"lat": -4.90,  "lon": 105.27, "provinsi": "Lampung",        "crop_type": "singkong","land_area_ha": 2.5},
    # Sulawesi
    {"lat": -5.14,  "lon": 119.43, "provinsi": "Sulawesi Selatan", "crop_type": "padi",   "land_area_ha": 2.5},
    {"lat": -5.14,  "lon": 119.43, "provinsi": "Sulawesi Selatan", "crop_type": "jagung", "land_area_ha": 2.0},
    {"lat": -0.89,  "lon": 119.87, "provinsi": "Sulawesi Tengah",  "crop_type": "padi",   "land_area_ha": 1.5},
    # Kalimantan
    {"lat": -3.32,  "lon": 114.59, "provinsi": "Kalimantan Selatan", "crop_type": "padi",    "land_area_ha": 1.8},
    {"lat":  0.02,  "lon": 109.34, "provinsi": "Kalimantan Barat",   "crop_type": "singkong","land_area_ha": 2.0},
    # NTB & NTT
    {"lat": -8.58,  "lon": 116.10, "provinsi": "NTB",  "crop_type": "padi",   "land_area_ha": 1.0},
    {"lat": -8.58,  "lon": 116.10, "provinsi": "NTB",  "crop_type": "jagung", "land_area_ha": 1.5},
    {"lat": -10.17, "lon": 123.61, "provinsi": "NTT",  "crop_type": "jagung", "land_area_ha": 1.2},
]

# Estimasi harvest_days & yield per komoditas (dari data Kementan historis)
# Ini dipakai sebagai ground truth untuk data tanpa feedback petani
HISTORICAL_STATS = {
    "padi":     {"harvest_days": 110, "yield_ton_per_ha": 5.2, "std_days": 15, "std_yield": 0.8},
    "jagung":   {"harvest_days": 100, "yield_ton_per_ha": 5.5, "std_days": 12, "std_yield": 0.9},
    "kedelai":  {"harvest_days":  88, "yield_ton_per_ha": 1.5, "std_days": 10, "std_yield": 0.3},
    "singkong": {"harvest_days": 280, "yield_ton_per_ha": 19.5, "std_days": 30, "std_yield": 2.5},
}


async def main():
    print("=" * 60)
    print("🌍 PanenCerdas — Seed data iklim dari NASA POWER")
    print("=" * 60)
    print(f"Total lokasi: {len(SAMPLE_LOCATIONS)}")
    print("Fetching data NASA POWER (mungkin 2–5 menit)...\n")

    # Buat folder data jika belum ada
    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    # Fetch semua lokasi
    climate_data = await fetch_bulk_for_training(SAMPLE_LOCATIONS, days_back=90)

    # Gabungkan dengan estimasi yield historis
    import numpy as np
    rng = np.random.default_rng(42)

    rows = []
    for i, (loc, climate) in enumerate(zip(SAMPLE_LOCATIONS, climate_data)):
        crop  = loc["crop_type"]
        stats = HISTORICAL_STATS[crop]

        # Tambah variasi realistis di sekitar nilai historis
        harvest_days = max(30, int(rng.normal(stats["harvest_days"], stats["std_days"])))
        yield_ton    = max(0.3, round(float(rng.normal(stats["yield_ton_per_ha"], stats["std_yield"])), 2))

        # Risk dari yield vs baseline
        ratio = yield_ton / stats["yield_ton_per_ha"]
        risk  = "low" if ratio >= 0.85 else ("medium" if ratio >= 0.65 else "high")

        rows.append({
            "ndvi":             climate.get("ndvi", estimate_ndvi_from_season(loc["lat"], loc["lon"], crop)),
            "rainfall_mm":      climate["rainfall_mm"],
            "temperature_c":    climate["temperature_c"],
            "solar_radiation":  climate["solar_radiation"],
            "land_area_ha":     loc["land_area_ha"],
            "crop_type":        crop,
            "harvest_days":     harvest_days,
            "yield_ton_per_ha": yield_ton,
            "risk_level":       risk,
            "provinsi":         loc["provinsi"],
            "data_source":      climate.get("data_source", "nasa_power"),
        })

        print(f"  ✓ [{i+1:2d}/{len(SAMPLE_LOCATIONS)}] {loc['provinsi']} — {crop} "
              f"| suhu: {climate['temperature_c']}°C "
              f"| hujan: {climate['rainfall_mm']}mm "
              f"| sumber: {climate.get('data_source', '?')}")

    # Simpan ke CSV
    df = pd.DataFrame(rows)
    output_path = data_dir / "nasa_power_cache.csv"
    df.to_csv(output_path, index=False)

    print(f"\n✅ {len(rows)} baris data tersimpan di: {output_path}")
    print(f"   Sumber data: {df['data_source'].value_counts().to_dict()}")

    # Buat template Kementan (diisi manual)
    _create_kementan_template(data_dir)

    print("\n📋 Langkah selanjutnya:")
    print("   1. (Opsional) Isi data/kementan_template.csv dengan data Kementan nyata")
    print("      lalu rename jadi kementan_produksi.csv")
    print("   2. Jalankan: python train.py")
    print("   3. Jalankan: python main.py")


def _create_kementan_template(data_dir: Path):
    """Buat template CSV untuk data Kementan (diisi manual dari website Kementan)."""
    template_path = data_dir / "kementan_template.csv"

    headers = [
        "ndvi", "rainfall_mm", "temperature_c", "solar_radiation",
        "land_area_ha", "crop_type", "harvest_days", "yield_ton_per_ha",
        "risk_level", "provinsi", "tahun", "data_source"
    ]

    example_rows = [
        [0.68, 180, 27.5, 195, 1.5, "padi",   105, 5.3, "low",    "Jawa Timur", 2023, "kementan"],
        [0.55, 95,  29.0, 210, 2.0, "jagung", 98,  5.1, "medium", "Jawa Tengah",2023, "kementan"],
        [0.50, 120, 28.0, 185, 0.8, "kedelai",90,  1.4, "medium", "Jawa Barat", 2023, "kementan"],
    ]

    with open(template_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(example_rows)

    print(f"\n📄 Template Kementan tersimpan di: {template_path}")
    print("   → Isi dengan data nyata dari kementan.go.id lalu rename ke kementan_produksi.csv")


if __name__ == "__main__":
    asyncio.run(main())
