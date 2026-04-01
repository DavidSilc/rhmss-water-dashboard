"""Scraper for the RHMSS reporting hydrological stations."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

_COORDS_FILE = Path(__file__).resolve().parent / "rhmss_coords.json"
_STATION_COORDS: dict[str, dict] = {}
if _COORDS_FILE.exists():
    with open(_COORDS_FILE, "r", encoding="utf-8") as _f:
        _STATION_COORDS = json.load(_f)

INDEX_URL = "https://www.hidmet.gov.rs/eng/hidrologija/izvestajne/index.php"
BASE_URL = "https://www.hidmet.gov.rs/eng/hidrologija/izvestajne/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

MAX_CONCURRENCY = 8
REQUEST_TIMEOUT = 15.0

_NUMBER_RE = re.compile(r"[-+]?\d+\.?\d*")


@dataclass
class StationEntry:
    """Minimal info parsed from the index page."""
    hm_id: str
    name: str
    river: str
    detail_url: str


@dataclass
class StationData:
    hm_id: str
    name: str
    river: str
    detail_url: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    water_stage_cm: Optional[float] = None
    change_cm: Optional[float] = None
    river_flow: Optional[float] = None
    water_temp: Optional[float] = None
    tendency: Optional[str] = None
    first_flood_alert_cm: Optional[float] = None
    second_flood_alert_cm: Optional[float] = None
    date: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Index parsing
# ---------------------------------------------------------------------------

def parse_index(html: str) -> list[StationEntry]:
    """Return a list of stations from the RHMSS index page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[StationEntry] = []
    seen_ids: set[str] = set()

    for link in soup.find_all("a", href=True):
        href: str = link["href"]
        m = re.search(r"hm(?:_|%5[Ff])id=(\d+)", href)
        if not m:
            continue
        hm_id = m.group(1)
        if hm_id in seen_ids:
            continue
        seen_ids.add(hm_id)

        name = link.get_text(strip=True)

        # River is in the next <td> sibling of the link's parent <td>
        river = ""
        td = link.find_parent("td")
        if td:
            next_td = td.find_next_sibling("td")
            if next_td:
                river = next_td.get_text(strip=True)

        detail_url = _resolve_detail_url(href)
        entries.append(StationEntry(hm_id=hm_id, name=name, river=river, detail_url=detail_url))

    return entries


def _resolve_detail_url(href: str) -> str:
    """Normalise a possibly-relative href from the index to a full URL."""
    if href.startswith("http"):
        return href
    return urljoin(BASE_URL, href)


# ---------------------------------------------------------------------------
# Detail-page parsing
# ---------------------------------------------------------------------------

def _extract_number(text: str) -> Optional[float]:
    """Pull the first number out of a string, or None."""
    m = _NUMBER_RE.search(text)
    if m:
        try:
            return float(m.group())
        except ValueError:
            return None
    return None


def _tendency_from_img(td) -> Optional[str]:
    """Map tendency image filename to a human label."""
    img = td.find("img")
    if not img:
        return None
    src = (img.get("src") or img.get("alt") or "").lower()
    if "rast" in src:
        return "rising"
    if "opad" in src:
        return "falling"
    if "stag" in src or "isti" in src:
        return "steady"
    return None


def parse_detail(html: str, entry: StationEntry) -> StationData:
    """Parse a single station detail page and return structured data."""
    soup = BeautifulSoup(html, "html.parser")
    coords = _STATION_COORDS.get(entry.name, {})
    data = StationData(
        hm_id=entry.hm_id,
        name=entry.name,
        river=entry.river,
        detail_url=entry.detail_url,
        lat=coords.get("lat"),
        lng=coords.get("lng"),
    )

    # Date line — look for a cell with "Date:" text
    date_td = soup.find("td", class_="plavapozadina")
    if date_td:
        data.date = date_td.get_text(strip=True).replace("\xa0", " ")

    # Locate the header row containing "Water stage" in a bela75 cell.
    # soup.find(string=...) won't work here because the cell has child tags
    # like <br/>, so we search by text content instead.
    water_stage_td = None
    for td in soup.find_all("td", class_="bela75"):
        if "Water stage" in td.get_text():
            water_stage_td = td
            break
    if not water_stage_td:
        data.error = "Could not locate water-stage header"
        return data

    header_tr = water_stage_td.find_parent("tr")
    if not header_tr:
        data.error = "No parent <tr> for water-stage header"
        return data

    # Walk forward through sibling <tr>s to find the data row (contains nivo.gif)
    data_tr = None
    for sibling in header_tr.find_next_siblings("tr"):
        if sibling.find("img", src=re.compile(r"nivo\.gif")):
            data_tr = sibling
            break

    if not data_tr:
        data.error = "Could not locate data row with nivo.gif"
        return data

    cells = data_tr.find_all("td", class_="bela75")

    # Cells order (matches all three page types observed):
    # 0 → water stage   1 → change   2 → river flow   3 → water temp
    if len(cells) >= 1:
        data.water_stage_cm = _extract_number(cells[0].get_text())
    if len(cells) >= 2:
        data.change_cm = _extract_number(cells[1].get_text())
    if len(cells) >= 3:
        data.river_flow = _extract_number(cells[2].get_text())
    if len(cells) >= 4:
        data.water_temp = _extract_number(cells[3].get_text())

    # Tendency & flood-alert row — follows a similar pattern below the data row
    tendency_tr = None
    for sibling in data_tr.find_next_siblings("tr"):
        imgs = sibling.find_all("img", src=re.compile(r"(rast|opad|stag)"))
        if imgs:
            tendency_tr = sibling
            break

    if tendency_tr:
        tcells = tendency_tr.find_all("td", class_="bela75")
        if len(tcells) >= 1:
            data.tendency = _tendency_from_img(tcells[0])
        if len(tcells) >= 3:
            data.first_flood_alert_cm = _extract_number(tcells[2].get_text())
        if len(tcells) >= 4:
            data.second_flood_alert_cm = _extract_number(tcells[3].get_text())

    return data


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def fetch_all_stations() -> list[dict]:
    """Scrape the index + all detail pages and return sorted station dicts."""
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async with httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        # 1. Fetch and parse the index page
        resp = await client.get(INDEX_URL)
        resp.raise_for_status()
        entries = parse_index(resp.text)

        # 2. Fetch detail pages concurrently
        async def _fetch_one(entry: StationEntry) -> StationData:
            async with sem:
                try:
                    r = await client.get(entry.detail_url)
                    r.raise_for_status()
                    return parse_detail(r.text, entry)
                except Exception as exc:
                    c = _STATION_COORDS.get(entry.name, {})
                    return StationData(
                        hm_id=entry.hm_id,
                        name=entry.name,
                        river=entry.river,
                        detail_url=entry.detail_url,
                        lat=c.get("lat"),
                        lng=c.get("lng"),
                        error=str(exc),
                    )

        results = await asyncio.gather(*[_fetch_one(e) for e in entries])

    # 3. Sort: numeric stage descending; None goes last
    stations = sorted(
        results,
        key=lambda s: (s.water_stage_cm is None, -(s.water_stage_cm or 0)),
    )

    return [asdict(s) for s in stations]


# ---------------------------------------------------------------------------
# RHMSS Water Level Forecast
# ---------------------------------------------------------------------------

FORECAST_URL = "https://www.hidmet.gov.rs/eng/prognoza/prognoza_voda.php"


def parse_forecast_table(html: str) -> dict:
    """Parse the RHMSS water level forecast HTML table."""
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table")
    if not table:
        return {"title": "", "columns": [], "rows": [], "error": "No table found"}

    rows = table.find_all("tr")
    if len(rows) < 6:
        return {"title": "", "columns": [], "rows": [], "error": "Table too short"}

    title_td = soup.find("td", class_="naslovHeader")
    title = title_td.get_text(strip=True) if title_td else ""

    date_cells = rows[2].find_all("td")
    day_names = [c.get_text(strip=True) for c in date_cells if c.get_text(strip=True)]

    date_labels = rows[3].find_all("td")
    dates = [c.get_text(strip=True) for c in date_labels if c.get_text(strip=True)]

    columns = ["River", "Station"]
    for i, d in enumerate(dates):
        label = f"{day_names[i]} {d}" if i < len(day_names) else d
        if i == 0:
            label += " (today)"
        columns.append(label)
    columns.extend(["1st Alert (cm)", "2nd Alert (cm)"])

    result_rows = []
    for row in rows[5:]:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue

        texts = []
        link = None
        for j, c in enumerate(cells):
            t = c.get_text(strip=True).replace("\xa0", " ")
            if j == 1:
                a = c.find("a", href=True)
                if a:
                    link = a["href"]
                    if not link.startswith("http"):
                        link = "https://www.hidmet.gov.rs/eng/prognoza/" + link
            texts.append(t)

        if not any(texts[2:]):
            continue

        result_rows.append({
            "river": texts[0] if len(texts) > 0 else "",
            "station": texts[1] if len(texts) > 1 else "",
            "values": texts[2:7] if len(texts) >= 7 else texts[2:],
            "first_alert": texts[7] if len(texts) > 7 else "",
            "second_alert": texts[8] if len(texts) > 8 else "",
            "detail_url": link,
        })

    return {
        "title": title,
        "columns": columns,
        "rows": result_rows,
        "error": None,
    }


async def fetch_forecast() -> dict:
    """Fetch and parse the RHMSS water level forecast table."""
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
    ) as client:
        try:
            resp = await client.get(FORECAST_URL)
            resp.raise_for_status()
            return parse_forecast_table(resp.text)
        except Exception as exc:
            return {"title": "", "columns": [], "rows": [], "error": str(exc)}
