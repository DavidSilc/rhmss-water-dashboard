"""FastAPI application — serves the API and static frontend."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .scrape import fetch_all_stations

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
