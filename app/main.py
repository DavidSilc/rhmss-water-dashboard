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
from .romanian_cache import romanian_cache
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


async def _romanian_data(field, fetch):
    return await romanian_cache.get(field, fetch)


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
    async def fetch():
        data = await fetch_danube_bulletin()
        return await fetch_bulletin_forecast(data.get("bulletin") or {})
    return await romanian_cache.get("bulletin", fetch)


@app.get("/api/inhga-forecast")
async def inhga_forecast():
    start = time.time()
    data = await _romanian_data("rows", fetch_danube_forecast)
    elapsed = round(time.time() - start, 1)
    return {"elapsed_seconds": elapsed, **data}
