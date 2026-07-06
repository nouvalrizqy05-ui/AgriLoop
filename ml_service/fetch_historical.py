"""
scripts/fetch_historical.py
---------------------------
Script sekali jalan untuk seed data iklim + NDVI historis.
- Data iklim (suhu, hujan, radiasi) : NASA POWER
- Data NDVI                          : NASA APPEEARS (MODIS MOD13Q1)

Jalankan SEBELUM train.py agar model pertama sudah punya data real.

Cara pakai:
  # NDVI real dari APPEEARS (perlu akun gratis, estimasi 10–20 menit)
  python scripts/fetch_historical.py

  # Pakai estimasi NDVI musiman saja (lebih cepat, tanpa akun APPEEARS)
  python scripts/fetch_historical.py --skip-ndvi

Output:
  data/nasa_power_cache.csv   ← data iklim + NDVI per lokasi sample
  data/kementan_template.csv       ← template CSV untuk isi data Kementan manual

Estimasi waktu:
  Dengan NDVI real  : ~15–25 menit (APPEEARS perlu waktu proses)
  Tanpa NDVI (--skip-ndvi) : ~2–5 menit
"""

import argparse
import asyncio
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from data_fetcher import fetch_bulk_for_training, estimate_ndvi_from_season

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── LOKASI SAMPLE ──────────────────────────────────────
SAMPLE_LOCATIONS = [
    # Jawa
    {"lat": -7.25,  "lon": 112.75, "provinsi": "Jawa Timur",         "crop_type": "padi",     "land_area_ha": 1.5},
    {"lat": -7.80,  "lon": 110.36, "provinsi": "Jawa Tengah",        "crop_type": "padi",     "land_area_ha": 1.2},
    {"lat": -6.90,  "lon": 107.60, "provinsi": "Jawa Barat",         "crop_type": "padi",     "land_area_ha": 1.8},
    {"lat": -7.25,  "lon": 112.75, "provinsi": "Jawa Timur",         "crop_type": "jagung",   "land_area_ha": 2.0},
    {"lat": -7.80,  "lon": 110.36, "provinsi": "Jawa Tengah",        "crop_type": "kedelai",  "land_area_ha": 0.8},
    {"lat": -6.20,  "lon": 106.82, "provinsi": "DKI Jakarta",        "crop_type": "padi",     "land_area_ha": 0.5},
    {"lat": -8.65,  "lon": 115.22, "provinsi": "Bali",               "crop_type": "padi",     "land_area_ha": 0.9},
    # Sumatera
    {"lat":  3.60,  "lon":  98.67, "provinsi": "Sumatera Utara",     "crop_type": "padi",     "land_area_ha": 2.0},
    {"lat": -0.95,  "lon": 100.36, "provinsi": "Sumatera Barat",     "crop_type": "padi",     "land_area_ha": 1.5},
    {"lat":  3.58,  "lon":  98.68, "provinsi": "Sumatera Utara",     "crop_type": "jagung",   "land_area_ha": 1.8},
    {"lat": -4.90,  "lon": 105.27, "provinsi": "Lampung",            "crop_type": "jagung",   "land_area_ha": 3.0},
    {"lat": -4.90,  "lon": 105.27, "provinsi": "Lampung",            "crop_type": "singkong", "land_area_ha": 2.5},
    # Sulawesi
    {"lat": -5.14,  "lon": 119.43, "provinsi": "Sulawesi Selatan",   "crop_type": "padi",     "land_area_ha": 2.5},
    {"lat": -5.14,  "lon": 119.43, "provinsi": "Sulawesi Selatan",   "crop_type": "jagung",   "land_area_ha": 2.0},
    {"lat": -0.89,  "lon": 119.87, "provinsi": "Sulawesi Tengah",    "crop_type": "padi",     "land_area_ha": 1.5},
    # Kalimantan
    {"lat": -3.32,  "lon": 114.59, "provinsi": "Kalimantan Selatan", "crop_type": "padi",     "land_area_ha": 1.8},
    {"lat":  0.02,  "lon": 109.34, "provinsi": "Kalimantan Barat",   "crop_type": "singkong", "land_area_ha": 2.0},
    # NTB & NTT
    {"lat": -8.58,  "lon": 116.10, "provinsi": "NTB",                "crop_type": "padi",     "land_area_ha": 1.0},
    {"lat": -8.58,  "lon": 116.10, "provinsi": "NTB",                "crop_type": "jagung",   "land_area_ha": 1.5},
    {"lat": -10.17, "lon": 123.61, "provinsi": "NTT",                "crop_type": "jagung",   "land_area_ha": 1.2},
]

HISTORICAL_STATS = {
    "padi":     {"harvest_days": 110, "yield_ton_per_ha": 5.2, "std_days": 15, "std_yield": 0.8},
    "jagung":   {"harvest_days": 100, "yield_ton_per_ha": 5.5, "std_days": 12, "std_yield": 0.9},
    "kedelai":  {"harvest_days":  88, "yield_ton_per_ha": 1.5, "std_days": 10, "std_yield": 0.3},
    "singkong": {"harvest_days": 280, "yield_ton_per_ha": 19.5, "std_days": 30, "std_yield": 2.5},
}


def parse_args():
    parser = argparse.ArgumentParser(description="PanenCerdas — Seed data historis")
    parser.add_argument(
        "--skip-ndvi",
        action="store_true",
        help="Lewati fetch NDVI dari APPEEARS, pakai estimasi musiman saja (lebih cepat)",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    print("=" * 65)
    print("🌍 PanenCerdas — Seed data iklim + NDVI historis")
    print("=" * 65)
    print(f"Total lokasi     : {len(SAMPLE_LOCATIONS)}")
    print(f"Sumber NDVI      : {'estimasi musiman (--skip-ndvi)' if args.skip_ndvi else 'NASA APPEEARS (MODIS)'}")
    print(f"Estimasi waktu   : {'2–5 menit' if args.skip_ndvi else '15–25 menit'}")
    print()

    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    # ── STEP 1: Fetch iklim dari NASA POWER ──────────────
    print("📡 [1/2] Fetching data iklim dari NASA POWER...")
    climate_data = await fetch_bulk_for_training(SAMPLE_LOCATIONS, days_back=90)
    print(f"   ✓ {len(climate_data)} lokasi selesai\n")

    # ── STEP 2: Fetch NDVI ────────────────────────────────
    if args.skip_ndvi:
        print("🌿 [2/2] Menggunakan estimasi NDVI musiman...")
        ndvi_data = [
            {
                "ndvi":        estimate_ndvi_from_season(loc["lat"], loc["lon"], loc["crop_type"]),
                "ndvi_source": "seasonal_estimate",
                "n_samples":   0,
            }
            for loc in SAMPLE_LOCATIONS
        ]
        print(f"   ✓ {len(ndvi_data)} estimasi selesai\n")
    else:
        print("🌿 [2/2] Fetching NDVI dari NASA APPEEARS (MODIS MOD13Q1)...")
        print("   Ini membutuhkan waktu 10–20 menit, harap tunggu...\n")
        try:
            from ndvi_fetcher import fetch_ndvi_bulk
            ndvi_data = await fetch_ndvi_bulk(SAMPLE_LOCATIONS, days_back=32)
        except ImportError:
            print("   ⚠️  ndvi_fetcher.py tidak ditemukan — pakai estimasi musiman")
            ndvi_data = [
                {
                    "ndvi":        estimate_ndvi_from_season(loc["lat"], loc["lon"], loc["crop_type"]),
                    "ndvi_source": "seasonal_estimate",
                    "n_samples":   0,
                }
                for loc in SAMPLE_LOCATIONS
            ]

        n_real = sum(1 for r in ndvi_data if r["ndvi_source"] == "modis_appeears")
        n_est  = len(ndvi_data) - n_real
        print(f"\n   ✓ NDVI selesai: {n_real} real (MODIS) + {n_est} estimasi musiman\n")

    # ── STEP 3: Gabungkan dan tambah yield historis ───────
    rng  = np.random.default_rng(42)
    rows = []

    for i, (loc, climate, ndvi_result) in enumerate(zip(SAMPLE_LOCATIONS, climate_data, ndvi_data)):
        crop  = loc["crop_type"]
        stats = HISTORICAL_STATS[crop]

        harvest_days = max(30, int(rng.normal(stats["harvest_days"], stats["std_days"])))
        yield_ton    = max(0.3, round(float(rng.normal(stats["yield_ton_per_ha"], stats["std_yield"])), 2))

        ratio = yield_ton / stats["yield_ton_per_ha"]
        risk  = "low" if ratio >= 0.85 else ("medium" if ratio >= 0.65 else "high")

        row = {
            "ndvi":             ndvi_result["ndvi"],
            "ndvi_source":      ndvi_result["ndvi_source"],
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
        }
        rows.append(row)

        print(
            f"  ✓ [{i+1:2d}/{len(SAMPLE_LOCATIONS)}] {loc['provinsi']:<25} {crop:<8}"
            f" | NDVI: {ndvi_result['ndvi']:.3f} [{ndvi_result['ndvi_source'][:6]}]"
            f" | suhu: {climate['temperature_c']}°C"
            f" | hujan: {climate['rainfall_mm']}mm"
        )

    # ── STEP 4: Simpan ────────────────────────────────────
    df = pd.DataFrame(rows)
    output_path = data_dir / "nasa_power_cache.csv"
    df.to_csv(output_path, index=False)

    print(f"\n✅ {len(rows)} baris data tersimpan di: {output_path}")

    ndvi_src_counts = df["ndvi_source"].value_counts().to_dict()
    climate_counts  = df["data_source"].value_counts().to_dict()
    print(f"   Sumber NDVI   : {ndvi_src_counts}")
    print(f"   Sumber iklim  : {climate_counts}")

    _create_kementan_template(data_dir)

    print("\n📋 Langkah selanjutnya:")
    print("   1. (Opsional) Isi data/kementan_template.csv dengan data Kementan nyata")
    print("      lalu rename jadi kementan_produksi.csv")
    print("   2. Jalankan: python train.py")
    print("   3. Jalankan: python main.py")


def _create_kementan_template(data_dir: Path):
    template_path = data_dir / "kementan_template.csv"
    headers = [
        "ndvi", "ndvi_source", "rainfall_mm", "temperature_c", "solar_radiation",
        "land_area_ha", "crop_type", "harvest_days", "yield_ton_per_ha",
        "risk_level", "provinsi", "tahun", "data_source"
    ]
    example_rows = [
        [0.68, "kementan_manual", 180, 27.5, 195, 1.5, "padi",    105, 5.3, "low",    "Jawa Timur",  2023, "kementan"],
        [0.55, "kementan_manual",  95, 29.0, 210, 2.0, "jagung",   98, 5.1, "medium", "Jawa Tengah", 2023, "kementan"],
        [0.50, "kementan_manual", 120, 28.0, 185, 0.8, "kedelai",  90, 1.4, "medium", "Jawa Barat",  2023, "kementan"],
    ]
    with open(template_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(example_rows)

    print(f"\n📄 Template Kementan tersimpan di: {template_path}")
    print("   → Isi dengan data nyata dari kementan.go.id lalu rename ke kementan_produksi.csv")


if __name__ == "__main__":
    asyncio.run(main())
