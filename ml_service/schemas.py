"""
schemas.py
----------
Pydantic models untuk input/output PanenCerdas ML Service.

Catatan integrasi (feat/integrate-mlservices-v2):
  - Field iklim (ndvi/rainfall_mm/temperature_c/solar_radiation) sekarang
    OPSIONAL. Jika kosong dan lat/lon juga kosong → main.py akan isi default
    Indonesia (lihat data_fetcher.INDONESIA_DEFAULTS).
  - Tambah pest_pressure & variety (sudah didukung model.py internal).
  - Tambah schema pemerintah (dashboard, predictions kabupaten, regions)
    supaya FastAPI bisa melayani frontend /pemerintah/* lewat router terpisah.
"""

from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Literal, Optional


CropType = Literal[
    "padi",
    "jagung",
    "kedelai",
    "ubi_jalar",
    "ubi_kayu",
    "cabe_besar",
    "cabe_rawit",
    "bawang_merah",
    "bawang_putih",
    "singkong",
]
RiskLevel = Literal["low", "medium", "high"]


# ── PREDIKSI INPUT ─────────────────────────────────────
class PredictInput(BaseModel):
    # Mutable agar main.py bisa override nilai iklim + NDVI dari NASA
    model_config = ConfigDict(frozen=False)

    crop_type: CropType = Field(
        ...,
        description="Jenis tanaman.",
        examples=["padi"],
    )

    @field_validator("crop_type", mode="before")
    @classmethod
    def _alias_singkong(cls, v):
        # "singkong" (frontend label lama) = "ubi_kayu" (label yang dipakai model v2.4)
        if v == "singkong":
            return "ubi_kayu"
        return v

    land_area_ha: float = Field(
        ...,
        gt=0.0,
        description="Luas lahan (hektar).",
        examples=[1.5],
    )

    ndvi: Optional[float] = Field(
        default=None,
        ge=0.0, le=1.0,
        description=(
            "NDVI (0.0-1.0). Opsional. Jika kosong → default 0.6 atau "
            "MODIS APPEEARS bila lat/lon ada."
        ),
        examples=[0.7],
    )
    rainfall_mm: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Curah hujan (mm). Opsional. Di-override NASA POWER bila lat/lon ada.",
        examples=[100.0],
    )
    temperature_c: Optional[float] = Field(
        default=None,
        ge=10.0, le=50.0,
        description="Suhu rata-rata (C). Opsional. Di-override NASA POWER bila lat/lon ada.",
        examples=[27.0],
    )
    solar_radiation: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Radiasi (MJ/m^2/hari). Opsional. Di-override NASA POWER bila lat/lon ada.",
        examples=[200.0],
    )

    pest_pressure: Optional[float] = Field(
        default=0.0,
        ge=0.0, le=1.0,
        description="Tingkat tekanan hama (0.0-1.0). Default 0.0.",
        examples=[0.3],
    )
    variety: Optional[str] = Field(
        default="Lokal",
        description="Nama varietas tanaman. Default 'Lokal'.",
        examples=["Ciherang"],
    )

    lat: Optional[float] = Field(
        default=None,
        ge=-11.0, le=6.0,
        description="Latitude (Indonesia: -11 s/d 6). Jika ada, iklim+NDVI di-fetch real.",
        examples=[-7.25],
    )
    lon: Optional[float] = Field(
        default=None,
        ge=95.0, le=141.0,
        description="Longitude (Indonesia: 95 s/d 141).",
        examples=[112.75],
    )


# ── PREDIKSI OUTPUT ────────────────────────────────────
class PredictOutput(BaseModel):
    # Pydantic v2 protects "model_*" namespace; V2 schema pakai `model_source`
    # jadi kita opt-out di sini.
    model_config = ConfigDict(protected_namespaces=())

    prediction_log_id: Optional[int] = None
    harvest_days: int
    yield_ton_per_ha: float
    total_yield_ton: float
    risk_level: RiskLevel
    risk_score: float = 0.0
    recommendations: list[str]
    model_source: Literal["ml_model", "fallback_rules"]
    confidence: float
    climate_source: Optional[str] = None
    ndvi_source: Optional[str] = None


# ── HEALTH CHECK ───────────────────────────────────────
class HealthResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    status: str
    model_loaded: bool
    service: str
    version: str
    feedback_stats: Optional[dict] = None
    cache_stats: Optional[dict] = None


# ── PEMERINTAH: DASHBOARD ──────────────────────────────
class KpiTile(BaseModel):
    label: str
    value: str
    delta: Optional[str] = None
    positive: bool = True


class DashboardSummary(BaseModel):
    province: str
    season: str
    tiles: list[KpiTile]


class YieldPoint(BaseModel):
    year: int
    value: float
    kind: Literal["aktual", "prediksi"]


class YieldTrend(BaseModel):
    province: str
    commodity: str
    unit: str = "juta ton"
    points: list[YieldPoint]


# ── PEMERINTAH: PREDIKSI KABUPATEN/KOTA ─────────────────────
class KabupatenPrediction(BaseModel):
    id: str
    kabupaten: str
    yield_pred_ton_per_ha: float
    luas_panen_ha: float
    produksi_pred_ton: float
    surplus_pct: float
    status: Literal["surplus", "cukup", "waspada", "defisit"]
    # Ground truth dari laporan panen petani (TrainingFeedback) yang lokasinya
    # jatuh di kabupaten ini. None kalau belum ada laporan masuk.
    yield_actual_ton_per_ha: Optional[float] = None
    feedback_count: int = 0


class PredictionsResponse(BaseModel):
    province: str
    commodity: str
    season: str
    items: list[KabupatenPrediction]


class NdviPoint(BaseModel):
    date: str
    ndvi: float


class KabupatenDetail(BaseModel):
    kabupaten: str
    yield_pred_ton_per_ha: float
    luas_panen_ha: float
    total_produksi_ton: float
    ndvi_series: list[NdviPoint]
    ndvi_source: Optional[Literal["modis_appeears", "seasonal_estimate"]] = (
        "seasonal_estimate"
    )
    backtest: list[YieldPoint]
    # MAPE backtest model vs aktual Kementan (rata-rata error % per tahun).
    # None kalau tidak ada tahun yang bisa diprediksi (mis. tak ada data iklim).
    backtest_mape: Optional[float] = None
    # Surplus/defisit + status pangan region ini (sama perhitungan dengan
    # KabupatenPrediction) — supaya halaman detail tidak perlu memuat list.
    surplus_pct: float = 0.0
    status: Literal["surplus", "cukup", "waspada", "defisit"] = "cukup"
    # Agregat laporan panen petani untuk kabupaten ini (None = belum ada).
    yield_actual_ton_per_ha: Optional[float] = None
    feedback_count: int = 0
