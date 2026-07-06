"""
build_province_geojson.py
-------------------------
One-shot builder for data/indonesia_provinces.geojson — real polygon boundaries
per provinsi, keyed by the SAME id the prediction endpoint emits
(``PROV_<kode-kementan>``) so the dashboard map can render filled choropleth
polygons instead of centroid bubbles.

Source: ans-4175/peta-indonesia-geojson (public domain, BAKOSURTANAL base,
34 provinsi modern). We deliberately match features by NAME (not the source
``kode``) because the source uses official BPS codes where 91=Papua / 92=Papua
Barat, whereas this project's provinces_data.py assigns them swapped. Matching
by name routes each polygon to the project's own code.

The 3 newest Papua provinces (Papua Selatan/Tengah/Pegunungan, dibentuk 2022)
are not in the source file; they keep falling back to Point centroids in
regions_router.py. That is intentional and honest — no boundaries, no polygon.

Run:  python scripts/build_province_geojson.py
Re-run only when refreshing the boundary data; the output file is committed.
"""

import json
import sys
import urllib.request
from pathlib import Path

# Make provinces_data importable when run from scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import provinces_data  # noqa: E402

SOURCE_URL = (
    "https://raw.githubusercontent.com/ans-4175/"
    "peta-indonesia-geojson/master/indonesia-prov.geojson"
)
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "indonesia_provinces.geojson"

# Coordinate precision: 3 decimals ~= 110 m, ample for a province-level map and
# keeps the payload small (the national view ships every province at once).
COORD_DECIMALS = 3

# Source name quirks that provinces_data.get() can't resolve on its own.
NAME_OVERRIDES = {
    "DI. ACEH": "ACEH",
    "NUSATENGGARA BARAT": "NUSA TENGGARA BARAT",
    "BANGKA BELITUNG": "KEPULAUAN BANGKA BELITUNG",
}


def _round_coords(node):
    """Recursively round every coordinate pair to COORD_DECIMALS."""
    if isinstance(node, (int, float)):
        return round(node, COORD_DECIMALS)
    return [_round_coords(child) for child in node]


def _resolve(name: str):
    raw = (name or "").strip()
    candidate = NAME_OVERRIDES.get(raw.upper(), raw)
    return provinces_data.get(candidate)


def main() -> int:
    print(f"Downloading source: {SOURCE_URL}")
    with urllib.request.urlopen(SOURCE_URL, timeout=90) as resp:
        src = json.load(resp)

    features = []
    matched, unmatched = [], []
    for f in src.get("features", []):
        name = f.get("properties", {}).get("Propinsi", "")
        prov = _resolve(name)
        if prov is None:
            unmatched.append(name)
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": f["geometry"]["type"],
                    "coordinates": _round_coords(f["geometry"]["coordinates"]),
                },
                "properties": {
                    "id": f"PROV_{prov.code}",
                    "code": prov.code,
                    "name": prov.name,
                    "kementan_name": prov.kementan_name,
                    "capital": prov.capital,
                    "region": prov.region,
                    "level": "province",
                },
            }
        )
        matched.append(prov.name)

    out = {"type": "FeatureCollection", "level": "province", "features": features}
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")

    size_kb = OUT_PATH.stat().st_size / 1024
    have = {p.code for p in provinces_data.all_provinces()}
    got = {f["properties"]["code"] for f in features}
    missing = sorted(have - got)

    print(f"Matched {len(matched)} provinsi -> {OUT_PATH.name} ({size_kb:.0f} KB)")
    if unmatched:
        print(f"Unmatched source features (skipped): {unmatched}")
    if missing:
        names = [provinces_data.by_code(c).name for c in missing]
        print(f"Provinsi without polygon (fallback to centroid bubble): {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
