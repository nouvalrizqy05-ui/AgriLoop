"""
regions_router.py
-----------------
Endpoints GeoJSON region untuk peta dashboard pemerintah.

Mode:
  - province = "ALL" / "Indonesia" -> polygon/centroid per provinsi (peta nasional)
  - province = nama provinsi        -> polygon kabupaten/kota provinsi itu
    (dari data/kabupaten_indonesia.geojson, GADM L2, id `KAB_<kode>`)

Provinsi tanpa polygon kabupaten di master -> fallback Point centroid provinsi.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

import provinces_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/regions", tags=["regions"])

PROVINCE_GEOJSON_PATH = Path(__file__).parent / "data" / "indonesia_provinces.geojson"
KAB_GEOJSON_PATH = Path(__file__).parent / "data" / "kabupaten_indonesia.geojson"


@lru_cache(maxsize=1)
def _kab_features_by_prov() -> dict[str, list[dict]]:
    """
    Map provinsi_kode (2-digit) -> list Feature polygon kabupaten/kota, dari
    data/kabupaten_indonesia.geojson (GADM L2). Properties dinormalisasi supaya
    `id` = ``KAB_<kode>`` nyambung dengan predictions_router untuk pewarnaan.
    """
    if not KAB_GEOJSON_PATH.exists():
        logger.warning(f"GeoJSON kabupaten tidak ditemukan: {KAB_GEOJSON_PATH}")
        return {}
    with open(KAB_GEOJSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    out: dict[str, list[dict]] = {}
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        kode = str(props.get("kode") or "")
        if len(kode) < 4:
            continue
        nama = props.get("nama")
        feat["properties"] = {
            "id":        f"KAB_{kode}",
            "kode":      kode,
            "kabupaten": nama,
            "name":      nama,
            "level":     "kabupaten",
        }
        out.setdefault(kode[:2], []).append(feat)
    logger.info(f"GeoJSON kabupaten loaded: {sum(len(v) for v in out.values())} kab/kota")
    return out


@lru_cache(maxsize=1)
def _province_polygons() -> dict[str, dict]:
    """
    Map id provinsi (``PROV_<code>``) -> Feature polygon real, dari
    data/indonesia_provinces.geojson (dibangun oleh
    scripts/build_province_geojson.py).

    Provinsi tanpa polygon di file (mis. 3 provinsi Papua baru) tidak ada di
    map ini; _province_features() otomatis fallback ke Point centroid.
    """
    if not PROVINCE_GEOJSON_PATH.exists():
        logger.warning(
            f"GeoJSON provinsi tidak ditemukan: {PROVINCE_GEOJSON_PATH} — "
            f"fallback ke centroid bubble untuk semua provinsi"
        )
        return {}
    with open(PROVINCE_GEOJSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    by_id = {
        f["properties"]["id"]: f
        for f in data.get("features", [])
        if f.get("properties", {}).get("id")
    }
    logger.info(f"GeoJSON provinsi loaded: {len(by_id)} polygon")
    return by_id


def _province_features(provinces: list[provinces_data.Province]) -> list[dict]:
    """
    Bangun list Feature per provinsi.

    Pakai polygon real kalau tersedia (choropleth terisi); kalau provinsi belum
    punya batas wilayah, fallback ke Point centroid (frontend render bubble).
    Keduanya pakai id ``PROV_<code>`` yang sama dengan predictions_router supaya
    pewarnaan status pangan tetap nyambung.
    """
    polygons = _province_polygons()
    features: list[dict] = []
    for p in provinces:
        polygon = polygons.get(f"PROV_{p.code}")
        if polygon is not None:
            features.append(polygon)
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type":        "Point",
                    "coordinates": [p.lon, p.lat],
                },
                "properties": {
                    "id":         f"PROV_{p.code}",
                    "code":       p.code,
                    "name":       p.name,
                    "kementan_name":   p.kementan_name,
                    "capital":    p.capital,
                    "region":     p.region,
                    "level":      "province",
                },
            }
        )
    return features


@router.get("/geojson")
def geojson(province: str = "DI Yogyakarta") -> dict:
    """
    Return GeoJSON sesuai mode (lihat module docstring).
    """
    key = (province or "").strip().upper()

    # Mode nasional: 37 provinsi sekaligus
    if key in ("ALL", "INDONESIA", "NASIONAL"):
        return {
            "type":     "FeatureCollection",
            "level":    "province",
            "features": _province_features(provinces_data.all_provinces()),
        }

    # Mode provinsi: drill-down → polygon kabupaten/kota provinsi itu
    prov = provinces_data.get(province)
    if not prov:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Provinsi '{province}' tidak dikenal. "
                f"Gunakan 'ALL' (peta nasional) atau nama provinsi resmi."
            ),
        )
    feats = _kab_features_by_prov().get(prov.code, [])
    if feats:
        return {
            "type":     "FeatureCollection",
            "level":    "kabupaten",
            "features": feats,
        }
    # Provinsi tanpa polygon kabupaten di master → fallback titik centroid provinsi
    return {
        "type":     "FeatureCollection",
        "level":    "province",
        "features": _province_features([prov]),
    }


@router.get("")
def list_regions(province: str = "DI Yogyakarta") -> dict:
    """Listing region (tanpa geometri). Berguna buat dropdown frontend."""
    key = (province or "").strip().upper()

    if key in ("ALL", "INDONESIA", "NASIONAL"):
        provs = provinces_data.all_provinces()
        return {
            "province": "Indonesia",
            "level":    "province",
            "count":    len(provs),
            "items": [
                {
                    "id":       f"PROV_{p.code}",
                    "code":     p.code,
                    "name":     p.name,
                    "capital":  p.capital,
                    "region":   p.region,
                }
                for p in provs
            ],
        }

    prov = provinces_data.get(province)
    if not prov:
        raise HTTPException(
            status_code=404,
            detail=f"Provinsi '{province}' tidak dikenal",
        )
    feats = _kab_features_by_prov().get(prov.code, [])
    if feats:
        return {
            "province": prov.name,
            "level":    "kabupaten",
            "count":    len(feats),
            "items":    [f["properties"] for f in feats],
        }
    return {
        "province": prov.name,
        "level":    "province",
        "count":    1,
        "items": [
            {
                "id":      f"PROV_{prov.code}",
                "code":    prov.code,
                "name":    prov.name,
                "capital": prov.capital,
                "region":  prov.region,
            }
        ],
    }


@router.get("/provinces", summary="Daftar 37 provinsi Indonesia")
def list_provinces() -> dict:
    """Index lengkap 37 provinsi (untuk dropdown frontend)."""
    provs = provinces_data.all_provinces()
    return {
        "count": len(provs),
        "items": [
            {
                "id":      f"PROV_{p.code}",
                "code":    p.code,
                "name":    p.name,
                "capital": p.capital,
                "region":  p.region,
                "lat":     p.lat,
                "lon":     p.lon,
            }
            for p in provs
        ],
    }
