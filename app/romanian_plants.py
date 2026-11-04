"""Dated plant generation, unit unavailability and operator reports for Romania."""

import asyncio
import math
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

POWERGRID = "https://www.powergrid.ro/api/"
SNN_FEED = "https://nuclearelectrica.ro/snn/en/feed/"
STARTUP = ("https://nuclearelectrica.ro/snn/en/2026/11/04/"
           "start-of-synchronizations-to-the-grid-procedures-for-unit-1-at-the-cernavoda-nuclear-power-plant/")
BASE_REPORTS = {
    "1": {"date": "2026-11-04", "url": STARTUP,
          "title": "Startup procedures began. Grid reconnection was scheduled for 6 November."},
    "2": {"date": "2026-11-04", "url": STARTUP,
          "title": "Restart depends on a sustained rise in the Danube after Unit 1 starts."},
}


def _latest(points):
    points = [p for p in points if len(p) >= 2
              and isinstance(p[0], int) and p[0] > 0
              and type(p[1]) in (int, float) and math.isfinite(p[1])]
    if points:
        timestamp, mw, *_ = max(points, key=lambda p: p[0])
        return {"at": timestamp, "mw": mw}


def latest_reading(response):
    data = response.json()
    return _latest(data.get("puncte", [])) if data.get("ok") else None


def unit_generation(response):
    series = response.json()
    readings = {}
    codes = {"30WCERNCERN1---Y": "1", "30WCERNCERN2---T": "2"}
    times = next((s["xAxisValues"] for s in series if s.get("xAxisValues")), [])
    for entry in series:
        unit = codes.get(entry.get("EntsoeCode"))
        if unit:
            points = zip((t // 1000 for t in entry.get("xAxisValues", times)), entry["data"])
            reading = _latest(points)
            if reading:
                readings[unit] = reading
    return readings or None


def unit_notices(response):
    data = response.json()
    if not data.get("ok") or not isinstance(data.get("opriri"), list):
        return None
    units = {}
    for notice in data["opriri"]:
        unit = {"CNE__U1_RET_CA": "1", "CNE__U2_RET_CA": "2"}.get(notice.get("machine"))
        if unit and notice.get("acum") and notice.get("plant") == "NPP Cernavoda":
            if notice.get("at", 0) > units.get(unit, {}).get("at", -1):
                units[unit] = {key: notice.get(key) for key in ("at", "from", "to", "lost")}
    return {"as_of": data.get("actualizat", {}).get("opriri"), "units": units}


def operator_reports(response):
    items = ElementTree.fromstring(response.content).findall("./channel/item")
    if not items:
        return None
    reports = dict(BASE_REPORTS)
    for item in items:
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        if not link.startswith("https://nuclearelectrica.ro/") or not re.search(
            r"shut\s?down|shut down|reconnect|disconnect|synchroniz|nominal (?:power|capacity)|restart|outage",
            title, re.I
        ):
            continue
        units = re.findall(r"\bunit\s+([12])\b", title, re.I)
        if re.search(r"\bunits\s+[12]\s+(?:and|&)\s+[12]\b", title, re.I):
            units = ["1", "2"]
        date = parsedate_to_datetime(item.findtext("pubDate")).date().isoformat()
        for unit in units:
            if date > reports[unit]["date"] or (date == reports[unit]["date"]
                    and reports[unit]["url"] == STARTUP and link != STARTUP):
                reports[unit] = {"date": date, "url": link, "title": title}
    return reports


_sources = {
    "iron_gates_1": (POWERGRID + "units.php?statie=30W-CHE-PDF1---A&range=30d", latest_reading),
    "nuclear_total": (POWERGRID + "nuclear.php?range=24h", latest_reading),
    "notices": (POWERGRID + "stiri.php", unit_notices),
    "reports": (SNN_FEED, operator_reports),
}
_cache = {}
_lock = asyncio.Lock()


async def _read_source(client, key, url, parse):
    cached_at, previous = _cache.get(key, (0, None))
    if previous is not None and time.monotonic() - cached_at < 300:
        return previous
    try:
        response = await client.get(url)
        response.raise_for_status()
        result = parse(response)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, ElementTree.ParseError):
        result = None
    if result is not None:
        _cache[key] = time.monotonic(), result
        return result
    if time.monotonic() - cached_at < 86400:
        return previous


async def fetch_romanian_plants():
    sources = dict(_sources)
    today = datetime.now(timezone.utc).date()
    weeks = []
    for offset in (0, 7, 14):
        year, week, _ = (today - timedelta(days=offset)).isocalendar()
        key = f"week_nuclear_unit_{year}_{week:02}"
        weeks.append(key)
        sources[key] = (f"https://energy-charts.info/charts/power/data_unit/ro/{key}.json", unit_generation)
    for key in list(_cache):
        if key not in sources:
            _cache.pop(key, None)
    async with _lock, httpx.AsyncClient(timeout=7, follow_redirects=True) as client:
        values = await asyncio.gather(*(_read_source(client, key, *spec) for key, spec in sources.items()))
    data = dict(zip(sources, values))
    readings = {}
    for key in reversed(weeks):
        readings.update(data.pop(key) or {})
    data["unit_mw"] = readings
    data["reports"] = data["reports"] or BASE_REPORTS
    return data
