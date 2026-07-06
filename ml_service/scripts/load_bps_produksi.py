"""
load_bps_produksi.py
--------------------
Loader data produksi per kabupaten/kota dari BPS WebAPI (SIMDASI) ke tabel
Supabase `kabupaten_produksi`. Dipakai untuk migrasi level kabupaten (baseline,
produksi, status pangan per kab/kota).

DUA MODE:

1) FILE  -> parse file JSON yang sudah diunduh dari browser (boleh berisi
   beberapa objek JSON digabung + komentar // ala respons yang dipaste).
       python scripts/load_bps_produksi.py --files input.json --crop padi

2) FETCH -> tarik langsung dari API BPS. JALANKAN DI MESIN ANDA (bukan server
   yang IP-nya diblok WAF BPS). Butuh token WebAPI (env BPS_WEBAPI_TOKEN atau --token).

   a) Satu provinsi:
       python scripts/load_bps_produksi.py --fetch --crop padi \
           --years 2020 2021 2022 2023 2024 2025 \
           --wilayah 3400000 --id-tabel ZjZ6MXlacGJNR0JaaHBPRSs0TzNUdz09

   b) SELURUH provinsi (loop kode provinsi dari provinces_data) — padi nasional:
       python scripts/load_bps_produksi.py --fetch --crop padi --all-provinces \
           --years 2020 2021 2022 2023 2024 2025

   c) SELURUH provinsi x SEMUA komoditas yang id_tabel-nya sudah diisi di CROP_TABEL:
       python scripts/load_bps_produksi.py --fetch --all-crops --all-provinces \
           --years 2022 2023 2024 2025

Kolom dideteksi dari `nama_variabel` ("Luas Panen" / "Produktivitas" / "Produksi"),
jadi commodity-agnostic: untuk komoditas lain cukup isi id_tabel-nya di CROP_TABEL
(atau --crop X --id-tabel ...). id_tabel beda per komoditas; ambil dari URL tabel
di portal SIMDASI BPS (sama cara kamu dapat id_tabel padi). Per-request yang gagal
(provinsi tanpa data / timeout) dilewati & dilaporkan, tidak menggagalkan run.

yield_ton_per_ha dihitung = produksi_ton / luas_panen_ha (fallback produktivitas/10).
Baris total provinsi (kode_wilayah pola 34_00000) otomatis dilewati.

Atribusi: data bersumber dari API Badan Pusat Statistik (BPS).
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# pastikan import database (engine Supabase) jalan dari folder ml_service
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from database import engine  # noqa: E402

BASE_URL = "https://webapi.bps.go.id/v1/api/interoperabilitas/datasource/simdasi"

# id_tabel SIMDASI per komoditas (datasource id=25). Isi saat ketemu di portal
# SIMDASI BPS: buka tabel komoditasnya, id_tabel muncul di URL/permintaan jaringan
# (sama cara id_tabel padi didapat). Biarkan None untuk yang belum punya — komoditas
# ber-id None otomatis dilewati oleh --all-crops, dan padi/palawija/hortikultura
# punya ketersediaan data per-kabupaten yang berbeda (lihat MIGRATION_KABUPATEN.md).
CROP_TABEL = {
    "padi":         "ZjZ6MXlacGJNR0JaaHBPRSs0TzNUdz09",
    "jagung":       None,
    "kedelai":      None,
    "ubi_jalar":    None,
    "ubi_kayu":     None,
    "cabe_besar":   None,
    "cabe_rawit":   None,
    "bawang_merah": None,
    "bawang_putih": None,
}


def _parse_num(s):
    """'18.836,00' -> 18836.0 ; '56,42' -> 56.42 ; '-'/'...'/'' -> None."""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s in ("-", "...", "NA", "–"):
        return None
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _iter_json_objects(raw: str):
    """Yield setiap objek JSON dalam teks (boleh banyak objek + komentar //)."""
    # buang komentar // (baris) — aman karena value tidak mengandung //
    cleaned = re.sub(r"//[^\n\r]*", "", raw)
    dec = json.JSONDecoder()
    i, n = 0, len(cleaned)
    while i < n:
        while i < n and cleaned[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        obj, end = dec.raw_decode(cleaned, i)
        yield obj
        i = end


def _detect_columns(kolom: dict) -> dict:
    """Map peran -> id kolom berdasar nama_variabel (commodity-agnostic)."""
    roles = {}
    for col_id, meta in kolom.items():
        nama = (meta.get("nama_variabel") or "").lower()
        if "luas panen" in nama:
            roles["luas"] = col_id
        elif "produktivitas" in nama:
            roles["produktivitas"] = col_id
        elif "produksi" in nama:
            roles["produksi"] = col_id
    return roles


def _rows_from_response(obj: dict, crop: str):
    """Ekstrak baris (kode4, tahun, luas, produksi, yield) dari 1 respons SIMDASI."""
    blocks = [b for b in obj.get("data", []) if isinstance(b, dict) and "data" in b]
    out = []
    for blk in blocks:
        tahun = blk.get("tahun_data")
        cols = _detect_columns(blk.get("kolom", {}))
        if "luas" not in cols or "produksi" not in cols:
            print(f"  [skip] blok tanpa kolom luas/produksi (tahun={tahun})")
            continue
        for row in blk.get("data", []):
            kode = str(row.get("kode_wilayah", ""))
            if len(kode) < 4 or kode[2:4] == "00":  # 34_00000 = total provinsi
                continue
            kode4 = kode[:4]
            var = row.get("variables", {})

            def val(role):
                cid = cols.get(role)
                if not cid:
                    return None
                return _parse_num(var.get(cid, {}).get("value_raw") or var.get(cid, {}).get("value"))

            luas = val("luas")
            produksi = val("produksi")
            prod_tas = val("produktivitas")
            if luas and produksi:
                ytph = round(produksi / luas, 2)
            elif prod_tas is not None:
                ytph = round(prod_tas / 10.0, 2)  # ku/ha -> ton/ha
            else:
                ytph = None
            if luas is None and produksi is None:
                continue
            out.append({
                "kode": kode4, "crop": crop, "tahun": int(tahun),
                "luas": luas, "produksi": produksi, "yield": ytph,
            })
    return out


def _province_wilayah() -> list[str]:
    """Kode wilayah SIMDASI per provinsi = `{kode2}00000`, dari provinces_data."""
    import provinces_data
    return [f"{p.code}00000" for p in provinces_data.all_provinces()]


def _fetch(token, crop, years, wilayahs, id_tabel, ds_id):
    """Tarik (crop, tahun, wilayah) dari SIMDASI. Resilient: request gagal dilewati.

    Return (objs, failures) — objs list respons JSON, failures list keterangan gagal.
    """
    import httpx
    id_tabel = id_tabel or CROP_TABEL.get(crop)
    if not id_tabel:
        sys.exit(f"id_tabel untuk '{crop}' tidak diketahui. Isi CROP_TABEL atau beri --id-tabel.")
    objs, failures = [], []
    total = len(years) * len(wilayahs)
    i = 0
    for w in wilayahs:
        for y in years:
            i += 1
            url = f"{BASE_URL}/id/{ds_id}/tahun/{y}/id_tabel/{id_tabel}/wilayah/{w}/key/{token}"
            print(f"  [{i:3d}/{total}] {crop} tahun {y} wilayah {w} ...")
            try:
                r = httpx.get(url, timeout=60)
                r.raise_for_status()
                objs.append(r.json())
            except Exception as e:
                msg = f"{crop} {y} wil={w}: {e}"
                print(f"      [lewati] {msg}")
                failures.append(msg)
    return objs, failures


def main():
    ap = argparse.ArgumentParser(description="Load BPS SIMDASI -> kabupaten_produksi")
    ap.add_argument("--crop", default="padi")
    ap.add_argument("--files", nargs="*", help="file JSON hasil unduh (mode file)")
    ap.add_argument("--fetch", action="store_true", help="tarik langsung dari API BPS")
    ap.add_argument("--years", nargs="*", type=int, help="tahun (mode fetch)")
    ap.add_argument("--wilayah", default="3400000", help="kode wilayah (prov+00000), mode fetch")
    ap.add_argument("--all-provinces", action="store_true",
                    help="loop SEMUA provinsi (kode dari provinces_data), mode fetch")
    ap.add_argument("--all-crops", action="store_true",
                    help="loop semua komoditas yang id_tabel-nya terisi di CROP_TABEL")
    ap.add_argument("--id-tabel", dest="id_tabel", help="id_tabel SIMDASI (per komoditas)")
    ap.add_argument("--ds-id", default="25", help="datasource id SIMDASI (default 25)")
    ap.add_argument("--token", default=os.getenv("BPS_WEBAPI_TOKEN"), help="token WebAPI BPS")
    ap.add_argument("--dry-run", action="store_true", help="parse saja, tidak menulis DB")
    args = ap.parse_args()

    rows = []
    failures = []
    if args.fetch:
        if not args.token:
            sys.exit("Mode fetch butuh --token atau env BPS_WEBAPI_TOKEN.")
        if not args.years:
            sys.exit("Mode fetch butuh --years (mis. --years 2022 2023).")

        wilayahs = _province_wilayah() if args.all_provinces else [args.wilayah]

        if args.all_crops:
            if args.id_tabel:
                sys.exit("--all-crops tidak bisa dipakai bareng --id-tabel (ambigu). "
                         "Isi id_tabel tiap komoditas di CROP_TABEL.")
            crops = [c for c, t in CROP_TABEL.items() if t]
            if not crops:
                sys.exit("Tidak ada komoditas dengan id_tabel terisi di CROP_TABEL.")
            skipped = [c for c, t in CROP_TABEL.items() if not t]
            print(f"--all-crops: proses {crops}")
            if skipped:
                print(f"  (lewati, id_tabel kosong: {skipped})")
        else:
            crops = [args.crop]

        print(f"Wilayah: {len(wilayahs)} | Komoditas: {len(crops)} | Tahun: {args.years}")
        for crop in crops:
            id_tabel = None if args.all_crops else args.id_tabel
            objs, fails = _fetch(args.token, crop, args.years, wilayahs, id_tabel, args.ds_id)
            failures.extend(fails)
            for obj in objs:
                rows.extend(_rows_from_response(obj, crop))
    elif args.files:
        for fp in args.files:
            raw = Path(fp).read_text(encoding="utf-8")
            for obj in _iter_json_objects(raw):
                rows.extend(_rows_from_response(obj, args.crop))
    else:
        sys.exit("Pilih salah satu: --files <json...> ATAU --fetch.")

    if not rows:
        if failures:
            print(f"\n{len(failures)} request gagal:")
            for f in failures:
                print(f"  - {f}")
        sys.exit("Tidak ada baris kabupaten yang ter-parse.")

    # Ringkasan per (komoditas, tahun) — jangan dump ribuan baris saat nasional.
    from collections import Counter
    by_ct = Counter((r["crop"], r["tahun"]) for r in rows)
    print(f"\nTer-parse {len(rows)} baris kab/kota:")
    for (crop, tahun), n in sorted(by_ct.items()):
        print(f"  {crop:13s} {tahun}: {n} kab/kota")

    if args.dry_run:
        print("\n[dry-run] tidak menulis DB.")
        if failures:
            print(f"{len(failures)} request gagal (dilewati).")
        return

    sql = text(
        "INSERT INTO public.kabupaten_produksi "
        "(kode_kabupaten, crop_type, tahun, luas_panen_ha, produksi_ton, yield_ton_per_ha, source) "
        "VALUES (:kode, :crop, :tahun, :luas, :produksi, :yield, 'bps_simdasi') "
        "ON CONFLICT (kode_kabupaten, crop_type, tahun) DO UPDATE SET "
        "luas_panen_ha=EXCLUDED.luas_panen_ha, produksi_ton=EXCLUDED.produksi_ton, "
        "yield_ton_per_ha=EXCLUDED.yield_ton_per_ha, source=EXCLUDED.source"
    )
    # Buang kode kab/kota yang tidak ada di master `kabupaten` (mis. pemekaran
    # Papua yang belum ada di GADM). Kalau dibiarkan, FK violation membatalkan
    # SELURUH transaksi (current transaction is aborted) -> nyaris semua baris gagal.
    with engine.connect() as conn:
        valid_kodes = {row[0] for row in conn.execute(text("SELECT kode FROM public.kabupaten"))}
    known   = [r for r in rows if r["kode"] in valid_kodes]
    unknown = sorted({r["kode"] for r in rows if r["kode"] not in valid_kodes})
    if unknown:
        print(f"\n{len(unknown)} kode kab/kota tak ada di master `kabupaten` (dilewati): {unknown}")

    # Savepoint per baris: 1 baris error tidak meracuni batch.
    written, row_errs = 0, []
    with engine.connect() as conn:
        for r in known:
            try:
                with conn.begin_nested():
                    conn.execute(sql, r)
                written += 1
            except Exception as e:
                row_errs.append(f"{r['tahun']} {r['kode']}: {e}")
        conn.commit()
    print(f"\nOK: {written}/{len(rows)} baris di-upsert ke kabupaten_produksi "
          f"({len(known)} kode valid, {len(unknown)} kode dilewati).")
    if row_errs:
        print(f"{len(row_errs)} baris gagal tulis:")
        for e in row_errs[:20]:
            print(f"  - {e}")
    if failures:
        print(f"\n{len(failures)} request gagal (dilewati):")
        for f in failures[:30]:
            print(f"  - {f}")
        if len(failures) > 30:
            print(f"  ... (+{len(failures) - 30} lagi)")


if __name__ == "__main__":
    main()
