"""Quick check kredensial APPEEARS — login saja, tidak submit task."""

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load .env dari folder ml_service (parent dari scripts/)
load_dotenv(Path(__file__).parent.parent / ".env")

USER = os.getenv("APPEEARS_USER", "")
PASS = os.getenv("APPEEARS_PASS", "")


async def main() -> int:
    print(f"APPEEARS_USER : {USER}")
    print(f"APPEEARS_PASS : {'*' * len(PASS)} (len={len(PASS)})")
    print()

    if not USER or not PASS:
        print("ERROR: credentials kosong di .env")
        return 2

    print("Login ke APPEEARS...")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://appeears.earthdatacloud.nasa.gov/api/login",
            auth=(USER, PASS),
        )

    print(f"HTTP status: {r.status_code}")
    if r.status_code != 200:
        print(f"Body: {r.text[:400]}")
        return 1

    data = r.json()
    token = data.get("token", "")
    print(f"Token (15 char awal): {token[:15]}...")
    print(f"Expiration         : {data.get('expiration', '?')}")
    print()
    print("LOGIN BERHASIL — credentials valid")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
