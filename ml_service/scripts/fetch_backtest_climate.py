"""
fetch_backtest_climate.py
-------------------------
One-shot builder for data/historical_climate.csv — per-province, per-year
growing-season climate from NASA POWER, used by the real model backtest
(predictions_router._build_backtest).

Why pre-fetch into a CSV instead of calling NASA POWER at request time:
  - The backtest re-runs the model for ~5 historical years per region. Doing
    5 live NASA POWER calls inside the (already NDVI-heavy) detail endpoint
    would make the analisis page slow and flaky.
  - The weather of a past year is immutable, so a committed snapshot is fine
    and keeps the dashboard fast + offline-demoable.

Aggregation per (province centroid, year):
  - temperature_c   = mean daily T2M over the year
  - solar_radiation = mean daily ALLSKY_SFC_SW_DWN over the year
  - rainfall_mm     = mean daily PRECTOTCORR * 30  (30-day-equivalent total,
                      matching the live prediction's period_days=30 semantics)

Run:  python scripts/fetch_backtest_climate.py
Re-run only to refresh; the output CSV is committed.
"""

import asyncio
import csv
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import provinces_data  # noqa: E402

NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "historical_climate.csv"

YEARS = list(range(2019, 2026))  # 2019..2025 — covers the last-5 window of any crop
CONCURRENCY = 8


async def _fetch_year(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                      prov: "provinces_data.Province", year: int) -> dict | None:
    params = {
        "parameters": "T2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN",
        "community":  "AG",
        "longitude":  prov.lon,
        "latitude":   prov.lat,
        "start":      f"{year}0101",
        "end":        f"{year}1231",
        "format":     "JSON",
    }
    async with sem:
        for attempt in range(3):
            try:
                resp = await client.get(NASA_POWER_URL, params=params, timeout=60.0)
                resp.raise_for_status()
                props = resp.json()["properties"]["parameter"]
                temp = [v for v in props["T2M"].values() if v != -999]
                rain = [v for v in props["PRECTOTCORR"].values() if v != -999]
                rad  = [v for v in props["ALLSKY_SFC_SW_DWN"].values() if v != -999]
                if not temp:
                    raise ValueError("no valid temperature")
                row = {
                    "code":            prov.code,
                    "name":            prov.name,
                    "year":            year,
                    "temperature_c":   round(sum(temp) / len(temp), 1),
                    "rainfall_mm":     round((sum(rain) / len(rain)) * 30, 1) if rain else 0.0,
                    "solar_radiation": round(sum(rad) / len(rad), 1) if rad else 0.0,
                }
                print(f"  ok  {prov.code} {prov.name:<28} {year}")
                return row
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    print(f"  FAIL {prov.code} {prov.name:<28} {year}: {e}")
                    return None
                await asyncio.sleep(1.5 * (attempt + 1))
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
        writer = csv.DictWriter(
            f, fieldnames=["code", "name", "year", "temperature_c",
                           "rainfall_mm", "solar_radiation"],
        )
        writer.writeheader()
        writer.writerows(rows)

    expected = len(provinces_data.all_provinces()) * len(YEARS)
    print(f"\nWrote {len(rows)}/{expected} rows -> {OUT_PATH.name}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
