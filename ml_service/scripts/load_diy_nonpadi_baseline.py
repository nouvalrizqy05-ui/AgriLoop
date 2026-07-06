"""
load_diy_nonpadi_baseline.py
----------------------------
Isi `kabupaten_produksi` untuk 8 komoditas NON-PADI di DIY (pilot), supaya
non-padi DIY ikut punya baseline & backtest per kab/kota seperti padi.

Sumber = Data_Raw/produksi_<crop>.csv + lahan_<crop>.csv (provinsi x 2020-2024,
real — bukan 2015), baris "Daerah Istimewa Yogyakarta". DIY tidak punya angka
per-kabupaten untuk non-padi, jadi:

  - yield_ton_per_ha  = yield PROVINSI DIY tahun itu (dipakai apa adanya;
                        ini yang menentukan baseline & backtest).
  - luas_panen_ha     = luas provinsi DIYY tahun itu, DISEBAR ke 5 kab/kota
                        proporsional terhadap share luas panen PADI tiap kab
                        (proxy lahan pertanian). Total provinsi terjaga.
  - produksi_ton      = yield x luas (= produksi provinsi x share).

source = 'kementan_diy_prop' -> menandai ini estimasi disagregasi provinsi,
bukan cacah per-kabupaten. Frontend menampilkannya dengan badge "baseline
provinsi (DIY pilot)".

Run:
    cd ml_service
    ./venv/Scripts/python.exe scripts/load_diy_nonpadi_baseline.py            # tulis DB
    ./venv/Scripts/python.exe scripts/load_diy_nonpadi_baseline.py --dry-run  # cek saja
"""

import argparse
import csv
import sys
from pathlib import Path

ML_SERVICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ML_SERVICE))

from sqlalchemy import text  # noqa: E402
from database import engine  # noqa: E402

DATA_RAW = ML_SERVICE / "Data_Raw"
DIY_ROW_NAME = "daerah istimewa yogyakarta"
YEARS = [str(y) for y in range(2020, 2025)]  # kolom CSV "2020".."2024"
# Hanya pangan/umbi: produksi & lahan-nya align bersih 2020-2024.
# Hortikultura (cabe besar/rawit, bawang merah/putih) DIKECUALIKAN: file luas &
# produksi-nya beda rentang tahun dan tidak align (yield jadi salah), jadi tidak
# jujur dibuat per-tahun. Hortikultura tetap jalan via baseline provinsi/nasional
# tanpa backtest per-kab.
NONPADI = ["jagung", "kedelai", "ubi_jalar", "ubi_kayu"]

UPSERT = text(
    "INSERT INTO public.kabupaten_produksi "
    "(kode_kabupaten, crop_type, tahun, luas_panen_ha, produksi_ton, yield_ton_per_ha, source) "
    "VALUES (:kode, :crop, :tahun, :luas, :produksi, :yield, 'kementan_diy_prop') "
    "ON CONFLICT (kode_kabupaten, crop_type, tahun) DO UPDATE SET "
    "luas_panen_ha=EXCLUDED.luas_panen_ha, produksi_ton=EXCLUDED.produksi_ton, "
    "yield_ton_per_ha=EXCLUDED.yield_ton_per_ha, source=EXCLUDED.source"
)


def _num(s: str) -> float | None:
    """'169,431.00' -> 169431.0 (format US: koma ribuan, titik desimal)."""
    s = (s or "").strip().replace(",", "")
    if not s or s in ("-", "...", "&nbsp;"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v


def _diy_year_values(prefix: str, crop: str) -> dict[str, float]:
    """Baca Data_Raw/<prefix>_<crop>.csv, ambil baris DIY -> {tahun: nilai}."""
    path = DATA_RAW / f"{prefix}_{crop}.csv"
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("Provinsi") or "").strip().lower() == DIY_ROW_NAME:
                return {y: _num(row.get(y, "")) for y in YEARS}
    return {}


def _padi_shares() -> dict[str, float]:
    """Share luas panen PADI per kab DIY (tahun terbaru) -> proxy alokasi lahan."""
    rows = engine.connect().execute(text(
        "SELECT kode_kabupaten, luas_panen_ha, tahun FROM public.kabupaten_produksi "
        "WHERE crop_type='padi' AND LEFT(kode_kabupaten,2)='34' ORDER BY kode_kabupaten, tahun"
    )).fetchall()
    latest: dict[str, float] = {}
    for r in rows:  # ORDER BY tahun -> nilai terakhir menang
        if r.luas_panen_ha is not None:
            latest[r.kode_kabupaten] = float(r.luas_panen_ha)
    total = sum(latest.values())
    if total <= 0:
        sys.exit("Tidak ada luas padi DIY di kabupaten_produksi — load padi dulu.")
    return {k: v / total for k, v in latest.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    shares = _padi_shares()
    print(f"Share luas padi DIY ({len(shares)} kab): "
          + ", ".join(f"{k}={s:.3f}" for k, s in sorted(shares.items())))

    out_rows = []
    for crop in NONPADI:
        prod = _diy_year_values("produksi", crop)
        luas = _diy_year_values("lahan", crop)
        got = []
        for y in YEARS:
            p, l = prod.get(y), luas.get(y)
            if not p or not l:
                continue  # tahun nol/kosong (mis. cabe_besar 2024, bawang_putih) dilewati
            yld = p / l
            for kode, share in shares.items():
                lk = round(l * share, 2)
                out_rows.append({
                    "kode": kode, "crop": crop, "tahun": int(y),
                    "luas": lk, "produksi": round(yld * lk, 2), "yield": round(yld, 3),
                })
            got.append(y)
        print(f"  {crop:13s}: tahun {got if got else 'KOSONG (skip)'}")

    print(f"\nTotal {len(out_rows)} baris ({len(NONPADI)} komoditas x 5 kab x tahun tersedia).")
    if args.dry_run:
        print("[dry-run] tidak menulis DB.")
        return

    written = 0
    with engine.connect() as conn:
        for r in out_rows:
            with conn.begin_nested():
                conn.execute(UPSERT, r)
            written += 1
        conn.commit()
    print(f"OK: {written} baris di-upsert ke kabupaten_produksi (source=kementan_diy_prop).")


if __name__ == "__main__":
    main()
