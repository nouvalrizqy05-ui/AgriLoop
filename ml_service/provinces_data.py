"""
provinces_data.py
-----------------
Lookup tabel 37 provinsi Indonesia: kode Kementan + nama display + nama di
CSV Kementan + centroid lat/lon administratif + ibukota.

Centroid lat/lon = titik tengah area administratif provinsi (referensi
publik: Wikipedia "List of provinces of Indonesia", Google Maps). Dipakai
oleh predictions_router untuk fetch iklim NASA POWER ketika request
mode provinsi (non-DIY).

Provinsi Papua Barat Daya (kode 96, dibentuk 2022) belum punya data Kementan
produksi terpisah; sementara digabung dengan Papua Barat di CSV.

`kementan_name` = string persis di kolom `provinsi` CSV `kementan_produksi.csv`.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Province:
    code:        str   # Kementan 2-digit code
    name:        str   # Display name (Title Case)
    kementan_name:    str   # UPPERCASE persis di CSV Kementan
    lat:         float
    lon:         float
    capital:     str
    region:      str   # "Sumatera" | "Jawa" | "Bali-NT" | "Kalimantan" | "Sulawesi" | "Maluku-Papua"


PROVINCES: list[Province] = [
    # ── SUMATERA ─────────────────────────────────────────
    Province("11", "Aceh",                 "ACEH",                 4.6951,  96.7494, "Banda Aceh",      "Sumatera"),
    Province("12", "Sumatera Utara",       "SUMATERA UTARA",       2.1153,  99.5451, "Medan",           "Sumatera"),
    Province("13", "Sumatera Barat",       "SUMATERA BARAT",      -0.7399, 100.8000, "Padang",          "Sumatera"),
    Province("14", "Riau",                 "RIAU",                 0.2933, 101.7068, "Pekanbaru",       "Sumatera"),
    Province("15", "Jambi",                "JAMBI",               -1.6101, 103.6131, "Jambi",           "Sumatera"),
    Province("16", "Sumatera Selatan",     "SUMATERA SELATAN",    -3.3194, 103.9145, "Palembang",       "Sumatera"),
    Province("17", "Bengkulu",             "BENGKULU",            -3.5778, 102.3464, "Bengkulu",        "Sumatera"),
    Province("18", "Lampung",              "LAMPUNG",             -4.5586, 105.4068, "Bandar Lampung",  "Sumatera"),
    Province("19", "Kepulauan Bangka Belitung", "KEPULAUAN BANGKA BELITUNG", -2.7411, 106.4406, "Pangkalpinang", "Sumatera"),
    Province("21", "Kepulauan Riau",       "KEPULAUAN RIAU",       3.9456, 108.1428, "Tanjungpinang",   "Sumatera"),
    # ── JAWA ─────────────────────────────────────────────
    Province("31", "DKI Jakarta",          "DAERAH KHUSUS IBUKOTA JAKARTA", -6.2088, 106.8456, "Jakarta", "Jawa"),
    Province("32", "Jawa Barat",           "JAWA BARAT",          -6.9147, 107.6098, "Bandung",         "Jawa"),
    Province("33", "Jawa Tengah",          "JAWA TENGAH",         -7.1500, 110.1403, "Semarang",        "Jawa"),
    Province("34", "DI Yogyakarta",        "DAERAH ISTIMEWA YOGYAKARTA", -7.8754, 110.4262, "Yogyakarta", "Jawa"),
    Province("35", "Jawa Timur",           "JAWA TIMUR",          -7.5360, 112.2384, "Surabaya",        "Jawa"),
    Province("36", "Banten",               "BANTEN",              -6.4058, 106.0640, "Serang",          "Jawa"),
    # ── BALI & NUSA TENGGARA ─────────────────────────────
    Province("51", "Bali",                 "BALI",                -8.4095, 115.1889, "Denpasar",        "Bali-NT"),
    Province("52", "Nusa Tenggara Barat",  "NUSA TENGGARA BARAT", -8.6529, 117.3616, "Mataram",         "Bali-NT"),
    Province("53", "Nusa Tenggara Timur",  "NUSA TENGGARA TIMUR", -8.6574, 121.0794, "Kupang",          "Bali-NT"),
    # ── KALIMANTAN ───────────────────────────────────────
    Province("61", "Kalimantan Barat",     "KALIMANTAN BARAT",     0.0000, 111.5000, "Pontianak",       "Kalimantan"),
    Province("62", "Kalimantan Tengah",    "KALIMANTAN TENGAH",   -1.6815, 113.3823, "Palangkaraya",    "Kalimantan"),
    Province("63", "Kalimantan Selatan",   "KALIMANTAN SELATAN",  -3.0926, 115.2838, "Banjarmasin",     "Kalimantan"),
    Province("64", "Kalimantan Timur",     "KALIMANTAN TIMUR",     0.5380, 116.4194, "Samarinda",       "Kalimantan"),
    Province("65", "Kalimantan Utara",     "KALIMANTAN UTARA",     3.0731, 116.0413, "Tanjung Selor",   "Kalimantan"),
    # ── SULAWESI ─────────────────────────────────────────
    Province("71", "Sulawesi Utara",       "SULAWESI UTARA",       1.4748, 124.8421, "Manado",          "Sulawesi"),
    Province("72", "Sulawesi Tengah",      "SULAWESI TENGAH",     -1.4300, 121.4456, "Palu",            "Sulawesi"),
    Province("73", "Sulawesi Selatan",     "SULAWESI SELATAN",    -3.6688, 119.9740, "Makassar",        "Sulawesi"),
    Province("74", "Sulawesi Tenggara",    "SULAWESI TENGGARA",   -4.1449, 122.1746, "Kendari",         "Sulawesi"),
    Province("75", "Gorontalo",            "GORONTALO",            0.6999, 122.4467, "Gorontalo",       "Sulawesi"),
    Province("76", "Sulawesi Barat",       "SULAWESI BARAT",      -2.8441, 119.2321, "Mamuju",          "Sulawesi"),
    # ── MALUKU & PAPUA ───────────────────────────────────
    Province("81", "Maluku",               "MALUKU",              -3.2385, 130.1453, "Ambon",           "Maluku-Papua"),
    Province("82", "Maluku Utara",         "MALUKU UTARA",         0.6300, 127.9667, "Sofifi",          "Maluku-Papua"),
    Province("91", "Papua Barat",          "PAPUA BARAT",         -1.3361, 133.1747, "Manokwari",       "Maluku-Papua"),
    Province("92", "Papua",                "PAPUA",               -2.5916, 140.6690, "Jayapura",        "Maluku-Papua"),
    Province("93", "Papua Selatan",        "PAPUA SELATAN",       -7.6500, 138.5000, "Merauke",         "Maluku-Papua"),
    Province("94", "Papua Tengah",         "PAPUA TENGAH",        -3.7333, 136.1667, "Nabire",          "Maluku-Papua"),
    Province("95", "Papua Pegunungan",     "PAPUA PEGUNUNGAN",    -4.0967, 138.9500, "Jayawijaya",      "Maluku-Papua"),
]


# Index untuk lookup cepat
_BY_KEMENTAN_NAME: dict[str, Province] = {p.kementan_name: p for p in PROVINCES}
_BY_NAME:     dict[str, Province] = {p.name.upper(): p for p in PROVINCES}
_BY_CODE:     dict[str, Province] = {p.code: p for p in PROVINCES}

# Alias umum dari frontend / UI label
_ALIASES: dict[str, str] = {
    "DIY":            "34",
    "YOGYAKARTA":     "34",
    "DI YOGYAKARTA":  "34",
    "JOGJA":          "34",
    "DKI":            "31",
    "DKI JAKARTA":    "31",
    "JAKARTA":        "31",
    "JABAR":          "32",
    "JATENG":         "33",
    "JATIM":          "35",
    "NTB":            "52",
    "NTT":            "53",
    "KALBAR":         "61",
    "KALTENG":        "62",
    "KALSEL":         "63",
    "KALTIM":         "64",
    "KALTARA":        "65",
    "SULUT":          "71",
    "SULTENG":        "72",
    "SULSEL":         "73",
    "SULTRA":         "74",
    "SULBAR":         "76",
}


def get(province: str) -> Optional[Province]:
    """Resolve nama provinsi (case-insensitive + alias-aware) -> Province object."""
    if not province:
        return None
    key = province.strip().upper()

    if key in _BY_KEMENTAN_NAME:
        return _BY_KEMENTAN_NAME[key]
    if key in _BY_NAME:
        return _BY_NAME[key]
    if key in _BY_CODE:
        return _BY_CODE[key]
    if key in _ALIASES:
        return _BY_CODE[_ALIASES[key]]
    return None


def by_code(code: str) -> Optional[Province]:
    return _BY_CODE.get(code)


def all_provinces() -> list[Province]:
    return list(PROVINCES)


def is_diy(province: str) -> bool:
    p = get(province)
    return p is not None and p.code == "34"
