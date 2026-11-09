"""Refresh only validated, dated public readings. Failed sources keep their last good copy."""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.romanian_cache import data_date, read_snapshot, usable, write_snapshot
from app.scrape_inhga import fetch_bulletin_forecast, fetch_danube_bulletin, fetch_danube_forecast


async def main():
    directory = Path(sys.argv[1])
    results = await asyncio.gather(
        fetch_danube_bulletin(), fetch_danube_forecast(), return_exceptions=True)
    stations, rows = (r if isinstance(r, dict) else {} for r in results)
    try:
        bulletin = await fetch_bulletin_forecast(stations.get("bulletin") or {})
    except Exception:
        bulletin = {}
    if not usable("rows", rows) and usable("rows", bulletin):
        rows = bulletin
    updates = 0
    for key, data in (("stations", stations), ("rows", rows), ("bulletin", bulletin)):
        path = directory / f"{key}.json"
        previous = read_snapshot(path, key)
        if (usable(key, data) and data_date(key, data)
                and (not previous or data_date(key, data) >= data_date(key, previous["data"]))):
            write_snapshot(path, {"saved": int(time.time() * 1000), "data": data})
            print(f"{key}: saved source data dated {data_date(key, data)}", flush=True)
            updates += 1
        else:
            print(f"{key}: source unavailable, retained previous snapshot", flush=True)
    if updates == 0:
        raise SystemExit("All sources unavailable. Previous snapshots retained.")


if __name__ == "__main__":
    asyncio.run(main())
