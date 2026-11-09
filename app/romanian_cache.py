"""Last good Romanian readings, retained across upstream failures and restarts."""

import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx

SNAPSHOTS = Path(__file__).resolve().parent.parent / "static" / "romanian-data"
MIRROR = ("https://raw.githubusercontent.com/DavidSilc/rhmss-water-dashboard/"
          "codex/romanian-data/static/romanian-data/")
FIELDS = {"stations": "stations", "rows": "rows", "bulletin": "rows"}
log = logging.getLogger(__name__)


def usable(key, data):
    field = FIELDS[key]
    if (not isinstance(data, dict) or data.get("error") or not isinstance(data.get(field), list)
            or not data[field] or not all(isinstance(row, dict) for row in data[field])):
        return False
    if key == "stations":
        return any(row.get("water_stage_cm") is not None for row in data[field])
    return (isinstance(data.get("columns"), list) and len(data["columns"]) >= 5
            and any(isinstance(row.get("values"), list)
                    and any(v is not None and v != "" for v in row["values"][1:])
                    for row in data[field]))


def data_date(key, data):
    dates = [data.get("observation_date"), data.get("bulletin_date")]
    dates += [row.get("date") if key == "stations" else row.get("forecast_date")
              for row in data.get(FIELDS[key], [])]
    parsed = []
    for value in dates:
        if not isinstance(value, str):
            continue
        for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                parsed.append(datetime.strptime(value[:10], pattern).date().isoformat())
                break
            except ValueError:
                continue
    return max(parsed, default="")


def read_snapshot(path, key):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (isinstance(value.get("saved"), (int, float)) and value["saved"] > 0
                and usable(key, value.get("data")) and data_date(key, value["data"])):
            return value
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def write_snapshot(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        json.dump(value, f, ensure_ascii=False, allow_nan=False)
        f.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RomanianCache:
    def __init__(self, directory=None, seeds=SNAPSHOTS):
        self.directory = Path(directory or os.getenv(
            "ROMANIAN_CACHE_DIR", str(Path(tempfile.gettempdir()) / "rhmss-romanian-cache")))
        self.seeds = seeds
        self.values = {}
        self.tasks = {}
        self.checked = {}
        self.live = set()

    def _accept(self, key, snapshot):
        previous = self.values.get(key)
        rank = lambda s: (data_date(key, s["data"]), s["saved"])
        if previous is None or rank(snapshot) >= rank(previous):
            self.values[key] = snapshot
            return True
        return False

    def _load(self, key):
        if key not in self.values:
            for folder in (self.seeds, self.directory):
                saved = read_snapshot(folder / f"{key}.json", key)
                if saved:
                    self._accept(key, saved)

    async def _mirror(self, key):
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                response = await client.get(MIRROR + key + ".json")
                response.raise_for_status()
                value = response.json()
                if (isinstance(value.get("saved"), (int, float))
                        and 0 < value["saved"] <= time.time() * 1000 + 300000
                        and usable(key, value.get("data")) and data_date(key, value["data"])):
                    return value
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass

    async def _refresh(self, key, fetch):
        self.checked[key] = time.monotonic()
        try:
            # The independent mirror can recover data even when the hosting IP is blocked.
            results = await asyncio.gather(
                asyncio.wait_for(fetch(), timeout=40), self._mirror(key), return_exceptions=True)
            direct, mirror = results
            if isinstance(mirror, dict) and self._accept(key, mirror):
                self.live.discard(key)
            if usable(key, direct) and data_date(key, direct):
                snapshot = {"saved": int(time.time() * 1000), "data": direct}
                if self._accept(key, snapshot):
                    self.live.add(key)
            elif key not in self.values:
                log.warning("Romanian %s sources unavailable, no saved readings", key)
            else:
                self.live.discard(key)
            if key in self.values:
                try:
                    write_snapshot(self.directory / f"{key}.json", self.values[key])
                except OSError:
                    log.warning("Could not persist Romanian %s cache", key)
        finally:
            self.checked[key] = time.monotonic()

    async def get(self, key, fetch):
        self._load(key)
        task = self.tasks.get(key)
        if (task is None or task.done()) and time.monotonic() - self.checked.get(key, -float("inf")) >= 300:
            task = asyncio.create_task(self._refresh(key, fetch))
            self.tasks[key] = task
        if key not in self.values and task:
            await asyncio.shield(task)
        snapshot = self.values.get(key)
        if not snapshot:
            return {FIELDS[key]: [], "columns": [], "error": "Readings are temporarily unavailable."}
        data = {**snapshot["data"], "fetched_at": snapshot["saved"],
                "data_date": data_date(key, snapshot["data"])}
        if key not in self.live:
            data.update(savedAt=snapshot["saved"], stale=True)
        return data


romanian_cache = RomanianCache()
