"""
kementan_data.py
-----------
Reader untuk data Kementan produksi pangan (provinsi x crop x tahun).

Source: ml_service/data/kementan_produksi.csv — disusun dari publikasi Kementan
"Produksi Komoditas Tanaman Pangan & Hortikultura" 2021-2025, plus baris
2020 sebagai baseline historis. Kolom relevan:

    crop_type        - padi, jagung, kedelai, ubi_jalar, ubi_kayu,
                       cabe_besar, cabe_rawit, bawang_merah, bawang_putih
    provinsi         - nama provinsi UPPERCASE (e.g. "DAERAH ISTIMEWA YOGYAKARTA")
    tahun            - integer tahun
    produksi_ton     - total produksi (ton)
    luas_panen_ha    - total luas panen (hektar)
    yield_ton_per_ha - rata-rata yield = produksi / luas

Helper:
    load()                                -> pd.DataFrame (cached)
    provinces()                           -> list[str] semua provinsi
    crops()                               -> list[str] semua komoditas
    years()                               -> list[int] semua tahun
    summary(province, year)               -> ringkasan total per provinsi-tahun
    trend(province, commodity)            -> list[dict] yield per tahun
    province_yield(province, crop, year)  -> float | None
    latest_year()                         -> int tahun terbaru di data
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

import provinces_data


DATA_DIR = Path(__file__).parent / "data"
KEMENTAN_CSV  = DATA_DIR / "kementan_produksi.csv"


@lru_cache(maxsize=1)
def load() -> pd.DataFrame:
    """Load Kementan CSV ke DataFrame. Cached selama proses hidup."""
    if not KEMENTAN_CSV.exists():
        return pd.DataFrame(columns=[
            "crop_type", "provinsi", "tahun", "produksi_ton",
            "luas_panen_ha", "yield_ton_per_ha",
        ])
    df = pd.read_csv(KEMENTAN_CSV)
    df["provinsi"] = df["provinsi"].str.upper().str.strip()
    df["tahun"]    = df["tahun"].astype(int)
    return df


def provinces() -> list[str]:
    return sorted(load()["provinsi"].dropna().unique().tolist())


def crops() -> list[str]:
    return sorted(load()["crop_type"].dropna().unique().tolist())


def years() -> list[int]:
    return sorted(load()["tahun"].dropna().unique().tolist())


def latest_year() -> int:
    yrs = years()
    return yrs[-1] if yrs else 0


def latest_year_for(province: str, crop_type: str) -> Optional[int]:
    """Tahun terbaru yang punya data untuk kombinasi provinsi+komoditas."""
    df   = load()
    prov = _normalize_province(province)
    rows = df[(df["provinsi"] == prov) & (df["crop_type"] == crop_type)]
    if rows.empty:
        return None
    return int(rows["tahun"].max())


def _normalize_province(p: str) -> str:
    """Toleransi input frontend (casing/alias) -> nama persis di kolom CSV.

    Sumber kebenaran tunggal: provinces_data, yang menyimpan `kementan_name`
    (string persis di CSV) untuk tiap provinsi + alias umum. Ini mencegah
    mismatch seperti "DKI Jakarta" (display) vs "DAERAH KHUSUS IBUKOTA JAKARTA"
    (CSV) yang dulu bikin data provinsi tidak ketemu.
    """
    prov = provinces_data.get(p)
    if prov:
        return prov.kementan_name
    return (p or "").strip().upper()


def summary(province: str, year: Optional[int] = None) -> dict:
    """
    Ringkasan produksi provinsi.

    - `year` None -> ambil baris terbaru per-komoditas (karena Kementan rilis
                     padi lebih awal daripada hortikultura, kalau dipaksa
                     ke 1 tahun banyak komoditas akan kosong)
    - `year` diisi -> snapshot tepat di tahun itu

    Returns:
        {
          "province": "...",
          "year_range": [min_year, max_year],   # range yg dipakai per crop
          "by_crop": [
            {"crop_type": "padi", "year": 2025, "produksi_ton": ..., ...},
            ...
          ],
          "total_produksi_ton": float,
          "total_luas_panen_ha": float,
        }
    """
    df   = load()
    prov = _normalize_province(province)

    prov_rows = df[df["provinsi"] == prov]
    if prov_rows.empty:
        return {
            "province":            prov,
            "year_range":          [0, 0],
            "by_crop":             [],
            "total_produksi_ton":  0.0,
            "total_luas_panen_ha": 0.0,
        }

    if year is not None:
        rows = prov_rows[prov_rows["tahun"] == year]
    else:
        # Per-komoditas: ambil tahun terbaru yang ada untuk crop itu
        idx  = prov_rows.groupby("crop_type")["tahun"].idxmax()
        rows = prov_rows.loc[idx]

    by_crop = [
        {
            "crop_type":        r.crop_type,
            "year":             int(r.tahun),
            "produksi_ton":     float(r.produksi_ton),
            "luas_panen_ha":    float(r.luas_panen_ha),
            "yield_ton_per_ha": float(r.yield_ton_per_ha),
        }
        for r in rows.itertuples(index=False)
    ]
    if by_crop:
        ymin = min(c["year"] for c in by_crop)
        ymax = max(c["year"] for c in by_crop)
    else:
        ymin = ymax = 0

    return {
        "province":            prov,
        "year_range":          [ymin, ymax],
        "by_crop":             by_crop,
        "total_produksi_ton":  float(rows["produksi_ton"].sum()),
        "total_luas_panen_ha": float(rows["luas_panen_ha"].sum()),
    }


def trend(province: str, commodity: str) -> list[dict]:
    """
    Series produksi+yield per tahun untuk satu komoditas di satu provinsi.
    Diurutkan ascending tahun.
    """
    df   = load()
    prov = _normalize_province(province)
    rows = df[(df["provinsi"] == prov) & (df["crop_type"] == commodity)]
    rows = rows.sort_values("tahun")
    return [
        {
            "year":             int(r.tahun),
            "produksi_ton":     float(r.produksi_ton),
            "luas_panen_ha":    float(r.luas_panen_ha),
            "yield_ton_per_ha": float(r.yield_ton_per_ha),
        }
        for r in rows.itertuples(index=False)
    ]


def province_yield(
    province: str, crop_type: str, year: Optional[int] = None,
) -> Optional[float]:
    """Yield (ton/ha) provinsi-crop-tahun. None kalau tidak ada."""
    df   = load()
    prov = _normalize_province(province)
    yr   = year if year is not None else latest_year()

    rows = df[
        (df["provinsi"] == prov)
        & (df["crop_type"] == crop_type)
        & (df["tahun"] == yr)
    ]
    if rows.empty:
        return None
    return float(rows.iloc[0]["yield_ton_per_ha"])


def yoy_delta_pct(
    province: str,
    crop_type: Optional[str] = None,
    year_now: Optional[int] = None,
) -> Optional[float]:
    """
    % perubahan produksi dari tahun sebelumnya.

    - `crop_type` None  -> total produksi semua komoditas provinsi
    - `crop_type` diisi -> produksi komoditas itu saja
    - `year_now` None   -> pakai tahun terbaru yang ada datanya untuk
                           kombinasi provinsi+komoditas
    """
    df   = load()
    prov = _normalize_province(province)

    rows = df[df["provinsi"] == prov]
    if crop_type:
        rows = rows[rows["crop_type"] == crop_type]
    if rows.empty:
        return None

    yr   = year_now if year_now is not None else int(rows["tahun"].max())
    prev = yr - 1

    now = rows[rows["tahun"] == yr]["produksi_ton"].sum()
    bef = rows[rows["tahun"] == prev]["produksi_ton"].sum()
    if bef == 0:
        return None
    return round((now - bef) / bef * 100.0, 1)
