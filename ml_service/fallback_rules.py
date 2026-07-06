# fallback_rules.py
"""
Rule-based fallback prediction system.
Digunakan ketika model ML belum dilatih atau gagal dimuat.
Logika berbasis domain knowledge pertanian Indonesia.

v2.4:
  - Tambah CROP_PROFILES untuk:
    ubi_jalar, ubi_kayu, cabe_besar, cabe_rawit, bawang_merah, bawang_putih
"""

from schemas import PredictInput, PredictOutput


# ── KONSTANTA PER KOMODITAS ─────────────────────────────────────────────────
# base_yield      : rata-rata nasional (ton/ha) — sumber Kementan
# base_harvest    : hari panen dari tanam
# optimal_temp    : rentang suhu ideal (°C)
# optimal_rainfall: curah hujan ideal (mm/bulan)
# optimal_ndvi    : indeks vegetasi minimal untuk kondisi baik
#
# Catatan tanaman hortikultura (cabe, bawang):
#   - yield dalam ton/ha (cabe & bawang dihitung berat segar)
#   - harvest_days jauh lebih pendek dari pangan pokok
#   - sangat sensitif terhadap suhu & hujan berlebih
CROP_PROFILES = {
    # ── TANAMAN PANGAN POKOK ───────────────────────────────────────────────
    "padi": {
        "base_harvest_days": 110,
        "base_yield":        5.2,       # ton/ha GKP (Gabah Kering Panen)
        "optimal_temp":      (24, 30),
        "optimal_rainfall":  (150, 300),
        "optimal_ndvi":      0.60,
        "yield_min":         0.5,
        "yield_max":         12.0,
        "satuan":            "ton/ha (GKP)",
    },
    "jagung": {
        "base_harvest_days": 100,
        "base_yield":        5.8,       # ton/ha pipilan kering
        "optimal_temp":      (21, 30),
        "optimal_rainfall":  (100, 200),
        "optimal_ndvi":      0.55,
        "yield_min":         0.5,
        "yield_max":         15.0,
        "satuan":            "ton/ha (pipilan kering)",
    },
    "kedelai": {
        "base_harvest_days": 85,
        "base_yield":        1.5,       # ton/ha biji kering
        "optimal_temp":      (20, 30),
        "optimal_rainfall":  (100, 200),
        "optimal_ndvi":      0.50,
        "yield_min":         0.3,
        "yield_max":         5.0,
        "satuan":            "ton/ha (biji kering)",
    },

    # ── UMBI-UMBIAN ────────────────────────────────────────────────────────
    "ubi_jalar": {
        "base_harvest_days": 120,       # 4–5 bulan
        "base_yield":        15.0,      # ton/ha umbi segar — Kementan rata-rata nasional
        "optimal_temp":      (21, 30),
        "optimal_rainfall":  (100, 200),
        "optimal_ndvi":      0.50,
        "yield_min":         3.0,
        "yield_max":         35.0,
        "satuan":            "ton/ha (umbi segar)",
    },
    "ubi_kayu": {
        "base_harvest_days": 270,       # 9–12 bulan
        "base_yield":        20.0,      # ton/ha umbi segar — Kementan rata-rata nasional
        "optimal_temp":      (25, 32),
        "optimal_rainfall":  (100, 250),
        "optimal_ndvi":      0.50,
        "yield_min":         5.0,
        "yield_max":         50.0,
        "satuan":            "ton/ha (umbi segar)",
    },

    # ── CABAI ──────────────────────────────────────────────────────────────
    "cabe_besar": {
        "base_harvest_days": 90,        # mulai panen 75–90 HST, berlanjut hingga 120 HST
        "base_yield":        8.0,       # ton/ha buah segar — Kementan rata-rata nasional ~7–9 ton
        "optimal_temp":      (24, 30),
        "optimal_rainfall":  (100, 200),
        "optimal_ndvi":      0.55,
        "yield_min":         1.0,
        "yield_max":         20.0,
        "satuan":            "ton/ha (buah segar)",
        "catatan":           "Sangat sensitif hujan >250mm/bulan (busuk buah)",
    },
    "cabe_rawit": {
        "base_harvest_days": 75,        # lebih cepat dari cabe besar
        "base_yield":        6.0,       # ton/ha buah segar — lebih rendah dari cabe besar
        "optimal_temp":      (24, 32),  # toleran suhu lebih tinggi
        "optimal_rainfall":  (100, 200),
        "optimal_ndvi":      0.52,
        "yield_min":         1.0,
        "yield_max":         15.0,
        "satuan":            "ton/ha (buah segar)",
        "catatan":           "Lebih toleran kekeringan daripada cabe besar",
    },

    # ── BAWANG ────────────────────────────────────────────────────────────
    "bawang_merah": {
        "base_harvest_days": 65,        # 60–70 HST
        "base_yield":        9.5,       # ton/ha umbi segar — Kementan rata-rata nasional ~9–10 ton
        "optimal_temp":      (25, 32),
        "optimal_rainfall":  (80, 150), # KRITIS: sangat sensitif hujan berlebih
        "optimal_ndvi":      0.50,
        "yield_min":         2.0,
        "yield_max":         20.0,
        "satuan":            "ton/ha (umbi segar)",
        "catatan":           "Hujan >200mm/bulan → risiko busuk umbi sangat tinggi",
    },
    "bawang_putih": {
        "base_harvest_days": 100,       # 90–110 HST, lebih lama dari bawang merah
        "base_yield":        7.0,       # ton/ha umbi segar — Kementan rata-rata nasional
        "optimal_temp":      (15, 25),  # BERBEDA: butuh suhu lebih dingin (dataran tinggi)
        "optimal_rainfall":  (60, 120), # LEBIH KERING dari bawang merah
        "optimal_ndvi":      0.48,
        "yield_min":         2.0,
        "yield_max":         15.0,
        "satuan":            "ton/ha (umbi segar)",
        "catatan":           "Umumnya ditanam di dataran >600 mdpl. Suhu >28°C menghambat pembentukan umbi",
    },
}


# ── HELPER ─────────────────────────────────────────────────────────────────
def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _temp_factor(temp: float, opt_range: tuple) -> float:
    """Faktor koreksi suhu: 1.0 = optimal, < 1.0 = tidak optimal."""
    lo, hi = opt_range
    if lo <= temp <= hi:
        return 1.0
    elif temp < lo:
        # Bawang putih & tanaman dataran tinggi → penalti lebih besar jika terlalu hangat
        return _clamp(1.0 - (lo - temp) * 0.04, 0.5, 1.0)
    else:
        return _clamp(1.0 - (temp - hi) * 0.05, 0.4, 1.0)


def _rain_factor(rain: float, opt_range: tuple) -> float:
    """Faktor koreksi curah hujan."""
    lo, hi = opt_range
    if lo <= rain <= hi:
        return 1.0
    elif rain < lo:
        return _clamp(0.7 + (rain / lo) * 0.3, 0.4, 1.0)
    else:
        # Hortikultura (bawang, cabe) lebih sensitif hujan berlebih
        excess_ratio = (rain - hi) / hi
        return _clamp(1.0 - excess_ratio * 0.5, 0.3, 1.0)


def _ndvi_factor(ndvi: float, optimal_ndvi: float) -> float:
    """Faktor kesehatan tanaman dari NDVI."""
    if ndvi >= optimal_ndvi:
        return _clamp(0.9 + (ndvi - optimal_ndvi) * 0.5, 0.9, 1.1)
    else:
        return _clamp(ndvi / optimal_ndvi, 0.4, 0.9)


def _compute_risk(temp_f: float, rain_f: float, ndvi_f: float) -> tuple[str, float]:
    """Hitung risk level dan risk score."""
    health_score = (ndvi_f * 0.40) + (temp_f * 0.30) + (rain_f * 0.30)
    risk_score   = round(_clamp(1.0 - health_score, 0.0, 1.0), 3)

    if risk_score < 0.25:
        risk_level = "low"
    elif risk_score < 0.55:
        risk_level = "medium"
    else:
        risk_level = "high"

    return risk_level, risk_score


def _build_recommendations(
    data: PredictInput,
    risk_level: str,
    temp_f: float,
    rain_f: float,
    ndvi_f: float,
) -> list[str]:
    recs    = []
    profile = CROP_PROFILES[data.crop_type]

    # ── NDVI ──────────────────────────────────────────
    if data.ndvi < 0.4:
        recs.append("⚠ NDVI rendah — periksa kondisi tanaman dan lakukan pemupukan nitrogen")
    elif data.ndvi < profile["optimal_ndvi"]:
        recs.append("Pertimbangkan pemupukan susulan untuk meningkatkan kehijauan tanaman")

    # ── SUHU ──────────────────────────────────────────
    lo_t, hi_t = profile["optimal_temp"]
    if data.temperature_c > hi_t:
        if data.crop_type in ("bawang_putih",):
            recs.append(
                f"⚠ Suhu {data.temperature_c}°C terlalu tinggi untuk {data.crop_type} — "
                "tanaman ini butuh suhu <25°C. Pertimbangkan tanam di dataran lebih tinggi"
            )
        else:
            recs.append(
                f"Suhu {data.temperature_c}°C di atas optimal — "
                "pertimbangkan irigasi sore hari untuk pendinginan"
            )
    elif data.temperature_c < lo_t:
        recs.append(
            f"Suhu {data.temperature_c}°C di bawah optimal — "
            "pertimbangkan mulsa untuk menjaga kelembaban dan suhu tanah"
        )

    # ── HUJAN ──────────────────────────────────────────
    lo_r, hi_r = profile["optimal_rainfall"]
    if data.rainfall_mm < lo_r:
        recs.append(f"Curah hujan {data.rainfall_mm:.0f} mm kurang — aktifkan irigasi tambahan")
    elif data.rainfall_mm > hi_r:
        if data.crop_type in ("bawang_merah", "bawang_putih"):
            recs.append(
                f"🚨 Curah hujan {data.rainfall_mm:.0f} mm sangat berisiko untuk {data.crop_type} — "
                "tingkatkan drainase, pasang mulsa plastik, pertimbangkan naungan hujan"
            )
        elif data.crop_type in ("cabe_besar", "cabe_rawit"):
            recs.append(
                f"Curah hujan {data.rainfall_mm:.0f} mm berlebih — "
                "waspada busuk buah dan antraknosa, semprotkan fungisida preventif"
            )
        else:
            recs.append(
                f"Curah hujan {data.rainfall_mm:.0f} mm berlebih — "
                "pastikan drainase lahan berfungsi baik"
            )

    # ── RISK LEVEL ─────────────────────────────────────
    if risk_level == "high":
        recs.append("🚨 Risiko tinggi — konsultasikan dengan penyuluh pertanian setempat")
    elif risk_level == "medium":
        recs.append("Pantau kondisi lahan lebih intensif, minimal 2x seminggu")

    # ── CATATAN KHUSUS KOMODITAS ───────────────────────
    catatan = profile.get("catatan")
    if catatan and risk_level != "low":
        recs.append(f"ℹ {catatan}")

    if not recs:
        recs.append("✅ Kondisi lahan optimal — pertahankan manajemen saat ini")

    return recs


# ── PREDICT FALLBACK ────────────────────────────────────────────────────────
def predict_fallback(data: PredictInput) -> PredictOutput:
    """
    Rule-based prediction tanpa model ML.
    Akurasi lebih rendah tapi selalu tersedia.
    """
    profile = CROP_PROFILES[data.crop_type]

    temp_f = _temp_factor(data.temperature_c, profile["optimal_temp"])
    rain_f = _rain_factor(data.rainfall_mm,   profile["optimal_rainfall"])
    ndvi_f = _ndvi_factor(data.ndvi,           profile["optimal_ndvi"])

    perf = (temp_f * 0.30) + (rain_f * 0.30) + (ndvi_f * 0.40)

    harvest_days = int(profile["base_harvest_days"] / max(perf, 0.5))
    harvest_days = int(_clamp(harvest_days, 5, 400))

    yield_per_ha = round(profile["base_yield"] * perf, 2)
    yield_per_ha = _clamp(yield_per_ha, profile["yield_min"], profile["base_yield"] * 1.3)

    total_yield  = round(yield_per_ha * data.land_area_ha, 2)

    risk_level, risk_score = _compute_risk(temp_f, rain_f, ndvi_f)

    recs = _build_recommendations(data, risk_level, temp_f, rain_f, ndvi_f)

    confidence = round(0.55 + perf * 0.15, 2)

    return PredictOutput(
        harvest_days=int(harvest_days),
        yield_ton_per_ha=round(float(yield_per_ha), 2),
        total_yield_ton=round(float(total_yield), 2),
        risk_level=risk_level,
        risk_score=risk_score,
        recommendations=recs,
        model_source="fallback_rules",
        confidence=confidence,
    )
