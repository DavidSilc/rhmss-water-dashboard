"""Scraper for INHGA (Romanian) Danube hydrological station data.

Extracts live station data (water level, change, tendency, alert status) from
the interactive map on https://www.hidro.ro and enriches it with Baziaș flow
and forecast data from the daily Danube bulletin.
"""

from __future__ import annotations

import re
import json
import asyncio
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup

INHGA_HOME = "https://www.hidro.ro/"
BULLETIN_LIST_URL = (
    "https://www.hidro.ro/bulletin_type/diagnoza-si-prognoza-pentru-dunare"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}
REQUEST_TIMEOUT = 20.0

_TENDENCY_CODES = {0: "steady", 1: "falling", 2: "rising"}
_ALERT_CODES = {
    0: None,
    1: "ATTENTION",
    2: "FLOOD",
    3: "DANGER",
}


@dataclass
class INHGAStationData:
    station_id: str
    name: str
    river: str
    detail_url: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    water_stage_cm: Optional[float] = None
    change_cm: Optional[float] = None
    tendency: Optional[str] = None
    alert_level: Optional[str] = None
    first_alert_cm: Optional[int] = None
    second_alert_cm: Optional[int] = None
    date: Optional[str] = None
    error: Optional[str] = None


@dataclass
class INHGABulletinSummary:
    bulletin_date: Optional[str] = None
    bulletin_url: Optional[str] = None
    bazias_flow_m3s: Optional[float] = None
    bazias_tendency: Optional[str] = None
    bazias_monthly_avg_m3s: Optional[float] = None
    forecast_bazias_flow_m3s: Optional[float] = None
    forecast_bazias_tendency: Optional[str] = None
    forecast_monthly_avg_m3s: Optional[float] = None
    forecast_table_img: Optional[str] = None
    flow_chart_img: Optional[str] = None


def _clean_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"m\s*3\s*/\s*s", "m3/s", text)
    text = re.sub(r"(\d{2})\s+\.(\d{2})", r"\1.\2", text)
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"  +", " ", text)
    return text


def _extract_water_level(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"H\s*=\s*(-?\d+)", text)
    if m:
        return float(m.group(1))
    return None


def _extract_flow(text: str) -> Optional[float]:
    text = _clean_text(text)
    m = re.search(r"(\d[\d\s.]*)\s*m3/s", text)
    if m:
        raw = m.group(1).replace(" ", "").strip().rstrip(".")
        return float(raw)
    return None


def _extract_monthly_avg(text: str, last: bool = False) -> Optional[float]:
    text = _clean_text(text)
    _MONTHS = (
        "ianuarie|februarie|martie|aprilie|mai|iunie|"
        "iulie|august|septembrie|octombrie|noiembrie|decembrie"
    )
    matches = re.findall(
        rf"(?:{_MONTHS})\s*\((\d[\d\s.]*)\s*m3/s\)",
        text,
        re.IGNORECASE,
    )
    if not matches:
        m = re.search(
            r"media multianual[aă].*?\((\d[\d\s.]*)\s*m3/s\)",
            text,
            re.IGNORECASE,
        )
        if m:
            matches = [m.group(1)]
    if not matches:
        return None
    raw = matches[-1] if last else matches[0]
    raw = raw.replace(" ", "").strip().rstrip(".")
    return float(raw)


_TENDENCY_MAP_RO: dict[str, str] = {
    "creștere": "rising",
    "crestere": "rising",
    "scădere": "falling",
    "scadere": "falling",
    "staționar": "steady",
    "stationar": "steady",
    "staționare": "steady",
    "stationare": "steady",
    "staţionar": "steady",
    "staţionare": "steady",
}


def _parse_bazias_tendency(text: str) -> Optional[str]:
    for ro, en in _TENDENCY_MAP_RO.items():
        if ro in text.lower():
            return en
    return None


def _clean_station_name(raw: str) -> str:
    name = raw.replace("S.H.", "").replace("R. DUNARE", "").strip()
    name = re.sub(r"\s+", " ", name)
    return name.title()


def _parse_homepage(html: str) -> tuple[list[dict], dict]:
    """Extract `stations` and `station_data` JS variables from INHGA homepage."""
    stations_list: list[dict] = []
    station_data: dict = {}

    m = re.search(r'var\s+stations\s*=\s*JSON\.parse\(\'(.*?)\'\)', html, re.DOTALL)
    if m:
        raw = m.group(1).encode().decode("unicode_escape")
        stations_list = json.loads(raw)

    m = re.search(r'var\s+station_data\s*=\s*JSON\.parse\(\'(.*?)\'\)', html, re.DOTALL)
    if m:
        raw = m.group(1).encode().decode("unicode_escape")
        station_data = json.loads(raw)

    return stations_list, station_data


def _lookup_alert_thresholds(name: str) -> tuple[Optional[int], Optional[int]]:
    """Look up CA/CI thresholds by station name with fuzzy matching."""
    key = name.lower().replace(".", "").replace(" ", "")
    for thr_name, (ca, ci) in _ALERT_THRESHOLDS.items():
        norm = thr_name.lower().replace(".", "").replace(" ", "")
        if key.startswith(norm) or norm.startswith(key):
            return ca, ci
    return None, None


def _build_danube_stations(
    stations_list: list[dict], station_data: dict
) -> list[INHGAStationData]:
    idomm_map = {str(s["idomm"]): s for s in stations_list}

    results: list[INHGAStationData] = []
    for key, sd in station_data.items():
        full_name = sd.get("DENUMIRE_STATIE", "")
        if "DUNARE" not in full_name.upper():
            continue
        if "VALEA DUNAREA" in full_name.upper():
            continue

        name = _clean_station_name(full_name)
        station_id = str(sd.get("COD_STATIE", key))
        water_level = _extract_water_level(sd.get("TEXT1", ""))
        change = sd.get("TENDINTA_NIVEL")
        if isinstance(change, str):
            try:
                change = float(change)
            except ValueError:
                change = None
        elif isinstance(change, (int, float)):
            change = float(change)
        else:
            change = None

        tendency = _TENDENCY_CODES.get(sd.get("COD_TENDINTA"), None)
        alert_level = _ALERT_CODES.get(sd.get("COD_DEP_COTE"), None)
        date_str = sd.get("DATA", "")

        stn_meta = idomm_map.get(key, {})
        detail_url = INHGA_HOME
        lat = stn_meta.get("lat")
        lng = stn_meta.get("lng")

        ca, ci = _lookup_alert_thresholds(name)

        results.append(INHGAStationData(
            station_id=station_id,
            name=name,
            river="Dunăre",
            detail_url=detail_url,
            lat=lat,
            lng=lng,
            water_stage_cm=water_level,
            change_cm=change,
            tendency=tendency,
            alert_level=alert_level,
            first_alert_cm=ca,
            second_alert_cm=ci,
            date=date_str,
        ))

    results.sort(
        key=lambda s: (s.water_stage_cm is None, -(s.water_stage_cm or 0)),
    )
    return results


def _largest_image_source(img) -> str:
    """WordPress srcset entries are not necessarily ordered by image size."""
    candidates = []
    for entry in img.get("srcset", "").split(","):
        parts = entry.strip().split()
        if len(parts) == 2 and re.fullmatch(r"\d+w", parts[1]):
            candidates.append((int(parts[1][:-1]), parts[0]))
    return max(candidates)[1] if candidates else img["src"]


async def _fetch_bulletin_summary(client: httpx.AsyncClient) -> INHGABulletinSummary:
    summary = INHGABulletinSummary()
    try:
        resp = await client.get(BULLETIN_LIST_URL)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        bulletin_url = None
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "diagnoza-si-prognoza-hidrologica-pentru-dunare" in href.lower():
                bulletin_url = href
                break
        if not bulletin_url:
            return summary

        resp2 = await client.get(bulletin_url)
        resp2.raise_for_status()

        bsoup = BeautifulSoup(resp2.text, "html.parser")
        title_el = bsoup.find("h1")
        if title_el:
            dm = re.search(r"(\d{2}\.\d{2}\.\d{4})", title_el.get_text(strip=True))
            if dm:
                summary.bulletin_date = dm.group(1)
        summary.bulletin_url = bulletin_url

        content = bsoup.find("div", class_="entry-content") or bsoup.find("article") or bsoup

        imgs = content.find_all("img", src=True)
        for img in imgs:
            full_src = _largest_image_source(img)
            if not full_src.startswith("http"):
                continue
            w = img.get("width", "0")
            h = img.get("height", "0")
            try:
                w, h = int(w), int(h)
            except (ValueError, TypeError):
                w, h = 0, 0
            if h > w and h > 500:
                summary.forecast_table_img = full_src
            elif w > 0:
                summary.flow_chart_img = full_src

        full_text = _clean_text(content.get_text("\n", strip=True))

        diag_m = re.search(
            r"Situa[tţț]ia debitelor\b.*?(?=Prognoza debitelor|$)",
            full_text,
            re.IGNORECASE | re.DOTALL,
        )
        prog_m = re.search(
            r"Prognoza debitelor\b.*",
            full_text,
            re.IGNORECASE | re.DOTALL,
        )

        if diag_m:
            diag = diag_m.group(0)
            bazias_m = re.search(r"sec[tţț]iunea\s+Bazia[sşșş]\b[^.]*", diag, re.IGNORECASE)
            if bazias_m:
                baz = bazias_m.group(0)
                summary.bazias_tendency = _parse_bazias_tendency(baz)
                summary.bazias_flow_m3s = _extract_flow(baz)
            summary.bazias_monthly_avg_m3s = _extract_monthly_avg(diag)

        if prog_m:
            prog = prog_m.group(0)
            bazias_m = re.search(r"sec[tţț]iunea\s+Bazia[sşșş]\b[^.]*", prog, re.IGNORECASE)
            if bazias_m:
                baz = bazias_m.group(0)
                summary.forecast_bazias_tendency = _parse_bazias_tendency(baz)
                summary.forecast_bazias_flow_m3s = _extract_flow(baz)
            summary.forecast_monthly_avg_m3s = _extract_monthly_avg(prog, last=True)

    except Exception:
        pass

    return summary


async def fetch_danube_bulletin() -> dict:
    """Main entry: fetch INHGA Danube station data + bulletin summary."""
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
    ) as client:
        try:
            resp = await client.get(INHGA_HOME)
            resp.raise_for_status()
            stations_list, station_data = _parse_homepage(resp.text)

            danube_stations = _build_danube_stations(stations_list, station_data)
            bulletin = await _fetch_bulletin_summary(client)

            return {
                "stations": [asdict(s) for s in danube_stations],
                "bulletin": asdict(bulletin),
                "error": None,
            }

        except Exception as exc:
            return {
                "stations": [],
                "bulletin": asdict(INHGABulletinSummary()),
                "error": str(exc),
            }


# ---------------------------------------------------------------------------
# AFDJ Danube Forecast (structured table from afdj.ro)
# ---------------------------------------------------------------------------

AFDJ_URL = "https://www.afdj.ro/ro/cotele-dunarii"
AFDJ_XML_URL = "https://afdj.ro/ro/tabel_cotele_dunarii/xml"

# These stations have blank forecasts in the HTML table, but the XML feed
# represents their missing forecasts as five zeroes. Other stations can
# legitimately have a forecast water level of zero centimetres.
_AFDJ_UNFORECASTED = {
    "Sulina", "Rast", "Cetate", "Drobeta Turnu Severin", "Orsova",
    "Drencova", "Moldova Veche", "Bazias",
}

_ALERT_THRESHOLDS: dict[str, tuple[int, int]] = {
    "Tulcea":            (320, 410),
    "Isaccea":           (380, 508),
    "Galati":            (560, 600),
    "Braila":            (560, 610),
    "Harsova":           (580, 610),
    "Cernavoda":         (500, 600),
    "Calarasi":          (550, 620),
    "Oltenita":          (550, 630),
    "Giurgiu":           (570, 640),
    "Zimnicea":          (530, 610),
    "Turnu Magurele":    (500, 550),
    "Corabia":           (500, 550),
    "Bechet":            (550, 600),
    "Calafat":           (550, 600),
}


def _afdj_date(value: str) -> date:
    from datetime import datetime

    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return datetime.strptime(value, "%d/%m/%Y").date()


def _afdj_forecast(records: list[dict]) -> dict:
    """Align forecasts by their issue date, independently of observation dates."""
    if not records:
        raise ValueError("No station data found in AFDJ response")
    base_date = max(r["observed_date"] for r in records)
    horizon = max(len(r["forecasts"]) for r in records)
    days = [base_date + timedelta(days=i) for i in range(horizon + 1)]
    columns = ["Station", "Km"] + [
        f"{d.strftime('%a %d.%m')}{' (observed)' if i == 0 else ''}"
        for i, d in enumerate(days)
    ] + ["1st Alert (cm)", "2nd Alert (cm)"]
    rows = []
    issue_dates = set()
    for r in sorted(records, key=lambda r: float(r["km"]) if r["km"] else float("inf")):
        forecasts = {
            r["forecast_date"] + timedelta(days=i + 1): v
            for i, v in enumerate(r["forecasts"]) if v != ""
        }
        if forecasts:
            issue_dates.add(r["forecast_date"])
        ca, ci = _ALERT_THRESHOLDS.get(r["station"], (None, None))
        rows.append({
            "station": r["station"], "km": r["km"],
            "level": r["level"], "change": r["change"],
            "observed_date": r["observed_date"].isoformat(),
            "forecast_date": r["forecast_date"].isoformat(),
            "values": [r["level"] if r["observed_date"] == base_date else ""]
                      + [forecasts.get(d, "") for d in days[1:]],
            "first_alert": str(ca) if ca else "",
            "second_alert": str(ci) if ci else "",
        })
    if not any(any(r["values"][1:]) for r in rows):
        raise ValueError("AFDJ response contains no usable future forecasts")
    warning = None
    if any(d < base_date for d in issue_dates):
        warning = (
            "Some forecasts were issued before the latest observations. "
            "Values are shown on their actual forecast dates; missing days are blank."
        )
    return {"columns": columns, "rows": rows, "error": None,
            "observation_date": base_date.isoformat(),
            "forecast_dates": sorted(d.isoformat() for d in issue_dates),
            "warning": warning}


def _parse_afdj_xml(xml: str) -> dict:
    """Read AFDJ's public Drupal XML feed, including 24–120 hour forecasts."""
    root = ET.fromstring(xml)
    records = []
    for item in root.findall("./item"):
        def value(field: str) -> str:
            return (item.findtext(f"{field}/value") or "").strip()

        station = value("field_localitatea") or value("title")
        forecasts = [value(f"field_tendinta_{hours}h") for hours in (24, 48, 72, 96, 120)]
        if station in _AFDJ_UNFORECASTED and all(v in ("", "0") for v in forecasts):
            forecasts = [""] * 5
        records.append({
            "station": station, "km": value("field_km"),
            "level": value("field_cota"), "change": value("field_variatia"),
            "observed_date": _afdj_date(value("field_field_data_actualiz_cote")),
            "forecast_date": _afdj_date(value("field_data_actualizare_prognoze")),
            "forecasts": forecasts,
        })
    return _afdj_forecast(records)


def _parse_afdj_table(html: str) -> dict:
    """Parse the AFDJ Danube water levels + forecast table."""

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return {"columns": [], "rows": [], "error": "No table found on AFDJ page"}

    trs = table.find_all("tr")
    if len(trs) < 2:
        return {"columns": [], "rows": [], "error": "AFDJ table too short"}

    result_rows: list[dict] = []
    for tr in trs[1:]:
        cells = tr.find_all("td")
        if len(cells) < 12:
            continue
        raw = [c.get_text(strip=True) for c in cells]
        station = raw[0]
        km = raw[1]
        level_str = raw[2].replace("\xa0", " ").replace("cm", "").strip()
        change = raw[3]
        h24 = raw[6]
        h48 = raw[7]
        h72 = raw[8]
        h96 = raw[9]
        h120 = raw[10]

        result_rows.append({
            "station": station,
            "km": km,
            "level": level_str,
            "change": change,
            "observed_date": _afdj_date(raw[5]),
            "forecast_date": _afdj_date(raw[11]),
            "forecasts": [h24, h48, h72, h96, h120],
        })

    return _afdj_forecast(result_rows)


_bulletin_forecast_lock = asyncio.Lock()
_bulletin_forecast_cache: tuple[tuple, float, dict] | None = None


async def _forecast_images(client: httpx.AsyncClient, bulletin: INHGABulletinSummary):
    seen = set()
    if bulletin.forecast_table_img:
        original = re.sub(r"-\d+x\d+(?=\.[a-zA-Z]+$)", "", bulletin.forecast_table_img)
        for url in dict.fromkeys((original, bulletin.forecast_table_img)):
            seen.add(url)
            yield url

    # Some daily bulletins omit the image even though INHGA published the
    # original as an unattached upload. The OCR header verifies the issue date.
    day = datetime.strptime(bulletin.bulletin_date, "%d.%m.%Y").date().isoformat()
    try:
        response = await client.get(INHGA_HOME, params={
            "rest_route": "/wp/v2/media", "media_type": "image",
            "after": day + "T00:00:00", "before": day + "T23:59:59",
            "_fields": "source_url,media_details", "per_page": 100,
        })
        response.raise_for_status()
        uploads = response.json()
    except (httpx.HTTPError, ValueError):
        return
    if not isinstance(uploads, list):
        return
    for item in uploads:
        if not isinstance(item, dict):
            continue
        details, url = item.get("media_details"), item.get("source_url")
        if not isinstance(details, dict) or not isinstance(url, str):
            continue
        width, height = details.get("width"), details.get("height")
        if (isinstance(width, int) and isinstance(height, int)
                and 700 <= width <= 4000 and width < height <= 6000
                and url.startswith(INHGA_HOME + "wp-content/uploads/") and url not in seen):
            seen.add(url)
            yield url


async def _fetch_bulletin_forecast(client: httpx.AsyncClient, bulletin: INHGABulletinSummary) -> dict:
    """Cache numeric extraction for 15 minutes, keyed by the dated source image."""
    global _bulletin_forecast_cache
    if not (bulletin.bulletin_url and bulletin.bulletin_date):
        raise ValueError("No dated INHGA bulletin is available")
    key = (bulletin.bulletin_url, bulletin.bulletin_date, bulletin.forecast_table_img)
    async with _bulletin_forecast_lock:
        cached = _bulletin_forecast_cache
        if cached and cached[0] == key and time.monotonic() - cached[1] < 900:
            return cached[2]

        async for image_url in _forecast_images(client, bulletin):
            try:
                response = await client.get(image_url)
                response.raise_for_status()
                from .inhga_ocr import parse_bulletin_image
                records = await asyncio.to_thread(parse_bulletin_image, response.content, bulletin.bulletin_date)
                data = _afdj_forecast(records)
                missing = any("" in row["values"] for row in data["rows"])
                data.update({
                    "format": "table", "source_url": bulletin.bulletin_url,
                    "source": "INHGA bulletin", "bulletin_date": bulletin.bulletin_date,
                    "warning": "Some values could not be read reliably and are left blank." if missing else None,
                })
                _bulletin_forecast_cache = (key, time.monotonic(), data)
                return data
            except (httpx.HTTPError, ValueError):
                continue
    raise ValueError("INHGA forecast numbers could not be read reliably")


async def fetch_bulletin_forecast(summary: dict) -> dict:
    async with httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        try:
            return await _fetch_bulletin_forecast(client, INHGABulletinSummary(**summary))
        except (ValueError, httpx.HTTPError):
            return {"rows": [], "columns": [], "error": "INHGA bulletin forecast unavailable"}


async def fetch_danube_forecast() -> dict:
    """Use the public XML API when AFDJ's web page rejects server requests.

    If both AFDJ routes fail, extract dated station water levels from the
    original INHGA bulletin and return the same structured response.
    """
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
    ) as client:
        for url, parser in ((AFDJ_URL, _parse_afdj_table), (AFDJ_XML_URL, _parse_afdj_xml)):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                data = parser(resp.text)
                if data.get("error") or not data.get("rows"):
                    continue
                return {**data, "source_url": url, "format": "table"}
            except (httpx.HTTPError, ValueError, ET.ParseError):
                continue

        bulletin = await _fetch_bulletin_summary(client)
        try:
            return await _fetch_bulletin_forecast(client, bulletin)
        except ValueError:
            pass
        return {"columns": [], "rows": [], "error":
                "Romanian forecast sources are temporarily unavailable. Please try again later."}
