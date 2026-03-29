# RHMSS Water-Stage Dashboard

A local web dashboard that scrapes live hydrological data from the
[Republic Hydrometeorological Service of Serbia](https://www.hidmet.gov.rs/eng/hidrologija/izvestajne/index.php)
and displays all reporting stations sorted by current water stage (highest first).

## Quick start

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000> and click **Refresh** to load station data.

## Notes

- Data is scraped from a third-party government website and may break if
  they change their HTML structure.
- A full refresh fetches ~146 station pages with bounded concurrency and
  typically takes 30-60 seconds.
