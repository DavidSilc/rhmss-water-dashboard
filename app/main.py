"""FastAPI application — serves the API and static frontend."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .scrape import fetch_all_stations, fetch_forecast
from .romanian_plants import fetch_romanian_plants
from .scrape_inhga import fetch_bulletin_forecast, fetch_danube_bulletin, fetch_danube_forecast

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="RHMSS Water-Stage Dashboard")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/stations")
async def stations():
    start = time.time()
    data = await fetch_all_stations()
    elapsed = round(time.time() - start, 1)
    return {"elapsed_seconds": elapsed, "count": len(data), "stations": data}


@app.get("/api/romanian-plants")
async def romanian_plants():
    return await fetch_romanian_plants()


_romanian_cache = {}
_romanian_locks = {"stations": asyncio.Lock(), "rows": asyncio.Lock()}


async def _romanian_data(field, fetch):
    async with _romanian_locks[field]:
        cached = _romanian_cache.get(field)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[2]
        data = await fetch()
        if data.get(field) and not data.get("error"):
            _romanian_cache[field] = (time.monotonic(), int(time.time() * 1000), data)
            return data
        if cached and time.monotonic() - cached[0] < 86400:
            return {**cached[2], "savedAt": cached[1]}
        return data


@app.get("/api/inhga-danube")
async def inhga_danube():
    start = time.time()
    data = await _romanian_data("stations", fetch_danube_bulletin)
    elapsed = round(time.time() - start, 1)
    return {"elapsed_seconds": elapsed, **data}


@app.get("/api/forecast")
async def forecast():
    start = time.time()
    data = await fetch_forecast()
    elapsed = round(time.time() - start, 1)
    return {"elapsed_seconds": elapsed, **data}


@app.get("/api/inhga-bulletin-forecast")
async def inhga_bulletin_forecast():
    data = await _romanian_data("stations", fetch_danube_bulletin)
    return await fetch_bulletin_forecast(data.get("bulletin") or {})


@app.get("/api/inhga-forecast")
async def inhga_forecast():
    start = time.time()
    data = await _romanian_data("rows", fetch_danube_forecast)
    elapsed = round(time.time() - start, 1)
    return {"elapsed_seconds": elapsed, **data}
