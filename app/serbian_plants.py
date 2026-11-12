"""Dated Serbian plant generation and public unit-unavailability notices."""

import asyncio
import json
import math
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .romanian_cache import write_snapshot

SEED = Path(__file__).resolve().parent.parent / "static" / "serbian-generation.json"
CACHE_FILE = Path(tempfile.gettempdir()) / "rhmss-serbian-plants.json"
UNIT_CODES = {"34WEHR-BBASRG1-X": "Unit 1", "34WEHR-BBASRG2-U": "Unit 2"}
OUTAGE_NAMES = {
    "HE DJERDAP I": "Đerdap I", "HE DJERDAP II": "Đerdap II",
    "HE BAJINA BASTA": "Bajina Bašta", "RHE BAJINA BASTA": "RHE Bajina Bašta",
    "HE ZVORNIK": "Zvornik", "HE BISTRICA": "Bistrica", "HE POTPEC": "Potpeć",
    "HE KOKIN BROD": "Kokin Brod", "HE UVAC": "Uvac", "HE PIROT": "Pirot",
    "VLASINSKE HE": "Vlasina", "HE MEDJUVRSJE": "Međuvršje", "HE OVCAR BANJA": "Ovčar Banja",
}


def parse_generation(series):
    if not isinstance(series, list):
        return None
    shared_times = next((s.get("xAxisValues") for s in series if isinstance(s, dict)
                         and s.get("xAxisValues")), [])
    units = {}
    for entry in series:
        if not isinstance(entry, dict) or entry.get("EntsoeCode") not in UNIT_CODES:
            continue
        values = {}
        for stamp, mw in zip(entry.get("xAxisValues", shared_times), entry.get("data", [])):
            if (type(stamp) in (int, float) and math.isfinite(stamp) and stamp > 0
                    and type(mw) in (int, float) and math.isfinite(mw) and mw >= 0):
                values[int(stamp / 1000)] = mw
        units[entry["EntsoeCode"]] = values
    if set(units) != set(UNIT_CODES):
        return None
    common = set.intersection(*(set(points) for points in units.values()))
    if not common:
        return None
    at = max(common)
    readings = [{"name": UNIT_CODES[code], "mw": values[at]} for code, values in units.items()]
    return {"at": at, "mw": sum(unit["mw"] for unit in readings), "units": readings}


def parse_notices(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.otable")
    if not table or [th.get_text(strip=True) for th in table.select("thead th")][:3] != ["Unit", "Lost", "Period"]:
        return None
    plants = {}
    for row in table.select("tbody tr"):
        cells = row.find_all("td")
        if len(cells) != 4:
            continue
        name = "".join(cells[0].find_all(string=True, recursive=False)).strip().upper()
        plant = OUTAGE_NAMES.get(name)
        if not plant:
            continue
        description = cells[0].select_one(".osub")
        unit = description.get_text(" ", strip=True).partition("·")[2].strip() if description else ""
        notice = {"unit": unit, "lost": cells[1].get_text(" ", strip=True),
                  "period": cells[2].get_text(" ", strip=True), "status": cells[3].get_text(" ", strip=True)}
        notices = plants.setdefault(plant, [])
        if notice not in notices:
            notices.append(notice)
    return {"checked_at": int(time.time()), "plants": plants}


_lock = asyncio.Lock()
_checked = 0
_data = None


def _restore():
    result = {"generation": {}, "notices": None}
    for path in (SEED, CACHE_FILE):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved.get("generation"), dict):
                for name, reading in saved["generation"].items():
                    if (isinstance(reading, dict) and type(reading.get("mw")) in (int, float)
                            and type(reading.get("at")) in (int, float)
                            and reading["at"] > result["generation"].get(name, {}).get("at", 0)):
                        result["generation"][name] = reading
            notices = saved.get("notices")
            if (isinstance(notices, dict) and isinstance(notices.get("plants"), dict)
                    and type(notices.get("checked_at")) is int
                    and notices["checked_at"] > (result["notices"] or {}).get("checked_at", 0)):
                result["notices"] = notices
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return result


async def _fetch(client, url, parser):
    try:
        response = await client.get(url)
        response.raise_for_status()
        return parser(response)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        return None


async def fetch_serbian_plants():
    global _data, _checked
    async with _lock:
        if _data is None:
            _data = _restore()
        if time.monotonic() - _checked < 300:
            return _data
        today = datetime.now(timezone.utc).date()
        urls = []
        for offset in (0, 7, 14):
            year, week, _ = (today - timedelta(days=offset)).isocalendar()
            urls.append("https://energy-charts.info/charts/power/data_unit/rs/"
                        f"week_hydro_pumped_storage_unit_{year}_{week:02}.json")
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            results = await asyncio.gather(
                *(_fetch(client, url, lambda r: parse_generation(r.json())) for url in urls),
                _fetch(client, "https://euroenergy.live/en/country/serbia", lambda r: parse_notices(r.text)))
        for reading in results[:-1]:
            previous = _data["generation"].get("RHE Bajina Bašta", {})
            if reading and reading["at"] >= previous.get("at", 0):
                _data["generation"]["RHE Bajina Bašta"] = reading
        if results[-1] is not None:
            _data["notices"] = results[-1]
        try:
            write_snapshot(CACHE_FILE, _data)
        except OSError:
            pass
        _checked = time.monotonic()
        return _data
