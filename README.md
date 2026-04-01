# Hydro Dashboard — RHMSS & INHGA Danube

A local web dashboard that scrapes live hydrological data from two sources:

1. **RHMSS (Serbia)** — [Republic Hydrometeorological Service of Serbia](https://www.hidmet.gov.rs/eng/hidrologija/izvestajne/index.php)
   — all reporting stations sorted by current water stage (highest first).
2. **INHGA (Romania)** — [National Institute of Hydrology and Water Management](https://www.hidro.ro/)
   — daily Danube diagnosis & forecast bulletin with flow data at Baziaș
   (entry to Romania, near Đerdap / Iron Gates) and tendencies for downstream
   stations (Gruia, Calafat, Bechet, … Galați, Isaccea, Tulcea).

The INHGA Danube data fills a gap: key stations on the lower Danube and near
Đerdap / Iron Gates (Porțile de Fier) that are not covered by RHMSS but have
significant impact on the Serbian electricity market.

## Quick start

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000> and click **Refresh** to load station data.

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard UI |
| `GET /api/stations` | RHMSS station data (all ~146 Serbian stations) |
| `GET /api/inhga-danube` | INHGA Danube stations (live data + bulletin summary) |
| `GET /api/inhga-forecast` | INHGA Danube 5-day forecast (via AFDJ, 23 stations) |
| `GET /api/forecast` | RHMSS water level forecast (21 Serbian stations) |

## Notes

- Data is scraped from third-party government websites and may break if
  they change their HTML structure.
- A full refresh fetches ~146 RHMSS station pages with bounded concurrency and
  typically takes 30-60 seconds. The INHGA bulletin loads in ~2-3 seconds.
- The INHGA bulletin is published daily (usually before noon Romanian time)
  and contains prose text which is parsed into structured data.

---

Po naročilu za Miha Štendler.
