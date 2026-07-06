"""
fetch_backtest_ndvi.py
----------------------
One-shot builder for data/historical_ndvi.csv — per-province, per-year real
NDVI from MODIS MOD13Q1 (the same product the app already cites), used by the
backtest so the model's yearly prediction reflects actual vegetation greenness
instead of a hardcoded 0.65.

Source: ORNL DAAC MODIS Web Service (https://modis.ornl.gov/rst/api/v1/) —
free, no auth, synchronous. Unlike NASA APPEEARS (which the live app uses and
which polls for minutes per point), this returns a point time series instantly,
so prefetching 37 provinces x 7 years is feasible.

Per (province centroid, year): take the wet-season window (Jan 1 -> ~late May,
~10 sixteen-day composites — the API caps a request at 10) which captures the
main Indonesian growing-season NDVI peak, drop fill values (-3000), apply the
0.0001 scale, and average. That single representative NDVI per year is what the
backtest feeds the model.

Run:  python scripts/fetch_backtest_ndvi.py
Re-run only to refresh; the output CSV is committed.
"""

import asyncio
import csv
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import provinces_data  # noqa: E402

BASE_URL = "https://modis.ornl.gov/rst/api/v1/MOD13Q1/subset"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "historical_ndvi.csv"

YEARS = list(range(2019, 2026))
# Day 1..150 -> composites at days 1,17,...,145 = 10 readings (API max per call).
START_DOY, END_DOY = 1, 150
CONCURRENCY = 4
FILL = -3000
SCALE = 0.0001


async def _fetch_year(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                      prov: "provinces_data.Province", year: int) -> dict | None:
    params = {
        "latitude":  prov.lat,
        "longitude": prov.lon,
        "startDate": f"A{year}{START_DOY:03d}",
        "endDate":   f"A{year}{END_DOY:03d}",
        "band":      "250m_16_days_NDVI",
        "kmAboveBelow": 0,
        "kmLeftRight":  0,
    }
    async with sem:
        for attempt in range(3):
            try:
                resp = await client.get(BASE_URL, params=params,
                                        headers={"Accept": "application/json"},
                                        timeout=90.0)
                resp.raise_for_status()
                subset = resp.json().get("subset", [])
                vals = []
                for comp in subset:
                    raw = comp.get("data", [None])[0]
                    if raw is None or raw == FILL or raw < -2000 or raw > 10000:
                        continue
                    vals.append(raw * SCALE)
                if not vals:
                    raise ValueError("no valid NDVI composites")
                ndvi = max(0.05, min(0.95, sum(vals) / len(vals)))
                print(f"  ok  {prov.code} {prov.name:<28} {year}  ndvi={ndvi:.3f} (n={len(vals)})")
                return {"code": prov.code, "name": prov.name, "year": year,
                        "ndvi": round(ndvi, 3)}
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    print(f"  FAIL {prov.code} {prov.name:<28} {year}: {e}")
                    return None
                await asyncio.sleep(2.0 * (attempt + 1))
    return None


async def main() -> int:
    sem = asyncio.Semaphore(CONCURRENCY)
    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        tasks = [
            _fetch_year(client, sem, prov, year)
            for prov in provinces_data.all_provinces()
            for year in YEARS
        ]
        for res in await asyncio.gather(*tasks):
            if res is not None:
                rows.append(res)

    rows.sort(key=lambda r: (r["code"], r["year"]))
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "name", "year", "ndvi"])
        writer.writeheader()
        writer.writerows(rows)

    expected = len(provinces_data.all_provinces()) * len(YEARS)
    print(f"\nWrote {len(rows)}/{expected} rows -> {OUT_PATH.name}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
