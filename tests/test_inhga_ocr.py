import asyncio
import copy
from datetime import date
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from app import inhga_ocr, scrape_inhga


# Independently transcribed from the official 2 November bulletin, including
# negative signs and crossings through zero. Q(m3/s) lines are deliberately absent.
EXPECTED = {
    "Calafat": [-169, -163, -158, -153, -151, -153, -157, -159],
    "Bechet": [-102, -96, -89, -82, -74, -71, -73, -76],
    "Corabia": [-143, -138, -133, -127, -121, -117, -117, -120],
    "Turnu Magurele": [-118, -112, -105, -97, -89, -85, -85, -88],
    "Zimnicea": [-80, -73, -66, -57, -49, -44, -42, -44],
    "Giurgiu": [-187, -180, -172, -163, -154, -147, -143, -143],
    "Oltenita": [-153, -147, -141, -133, -126, -119, -116, -115],
    "Calarasi": [-188, -181, -173, -164, -155, -146, -141, -138],
    "Fetesti": [-210, -203, -196, -188, -179, -171, -165, -162],
    "Cernavoda": [-257, -250, -243, -234, -224, -214, -207, -202],
    "Harsova": [-148, -142, -134, -123, -112, -100, -91, -86],
    "Vadu Oii": [-114, -109, -100, -93, -89, -84, -80, -77],
    "Braila": [-18, -15, -9, -3, 1, 5, 9, 11],
    "Galati": [7, 9, 15, 21, 25, 28, 31, 33],
    "Grindu": [-6, -4, 2, 8, 12, 15, 18, 20],
    "Isaccea": [12, 14, 17, 22, 25, 27, 30, 31],
    "Tulcea": [8, 9, 11, 14, 16, 18, 19, 21],
}


def synthetic_grid():
    header = [("Station", 1), ("Unit", 1)] + [(f"{i:02}.11.2026", 1) for i in range(2, 10)]
    rows = [[(name, 1), ("H(cm)", 1)] + [(str(v), 1) for v in values] for name, values in EXPECTED.items()]
    return [header] + rows


class ImageExtractionTests(unittest.TestCase):
    def test_previous_day_is_read_without_using_todays_values(self):
        content = (Path(__file__).parent / "fixtures" / "inhga-2026-11-01.jpg").read_bytes()
        records = inhga_ocr.parse_bulletin_image(content, "01.11.2026")
        rows = {r["station"]: [r["level"]] + r["forecasts"] for r in records}
        self.assertEqual(len(rows), 17)
        self.assertEqual(rows["Tulcea"], ["5", "3", "3", "5", "6", "8", "10", "13"])
        self.assertEqual(rows["Braila"], ["-18", "-19", "-17", "-14", "-10", "-5", "1", "8"])
        self.assertEqual(rows["Grindu"], ["-6", "-7", "-5", "-2", "1", "5", "9", "14"])
        self.assertEqual(rows["Cernavoda"], ["-257", "-255", "-248", "-242", "-235", "-227", "-215", "-203"])
        # The low-confidence fourth forecast is intentionally omitted, not guessed.
        self.assertEqual(rows["Calafat"], ["-176", "-172", "-167", "-161", "", "-149", "-144", "-142"])

    def test_all_136_values_match_the_original_bulletin(self):
        content = (Path(__file__).parent / "fixtures" / "6-1.jpg").read_bytes()
        records = inhga_ocr.parse_bulletin_image(content, "02.11.2026")
        actual = {r["station"]: list(map(int, [r["level"]] + r["forecasts"])) for r in records}
        self.assertEqual(actual, EXPECTED)
        payload = scrape_inhga._afdj_forecast(records)
        self.assertEqual(len(payload["columns"]), 12)
        self.assertIn("09.11", payload["columns"][-3])
        self.assertEqual(payload["forecast_dates"], ["2026-11-02"])

    def test_mismatched_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            inhga_ocr._parse_grid(synthetic_grid(), "03.11.2026")

    def test_flow_rows_never_become_water_levels(self):
        grid = synthetic_grid()
        grid[1][1] = ("Q(mc/s)", 1)
        rows = inhga_ocr._parse_grid(grid, "02.11.2026")
        self.assertNotIn("Calafat", [r["station"] for r in rows])

    def test_uncertain_numeric_cell_is_blank_not_guessed(self):
        grid = synthetic_grid()
        grid[1][3] = ("163", 0.65)
        row = inhga_ocr._parse_grid(grid, "02.11.2026")[0]
        self.assertEqual(row["forecasts"][0], "")

    def test_unknown_station_and_incomplete_rows_are_rejected(self):
        grid = synthetic_grid()
        grid[1][0] = ("Another River", 1)
        grid[2][3:] = [("?", 0)] * 7
        rows = inhga_ocr._parse_grid(grid, "02.11.2026")
        self.assertEqual(len(rows), 15)

    def test_malformed_image_is_rejected(self):
        with self.assertRaises(ValueError):
            inhga_ocr.parse_bulletin_image(b"not an image", "02.11.2026")


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        scrape_inhga._bulletin_forecast_cache = None
        scrape_inhga._bulletin_forecast_lock = asyncio.Lock()
        self.bulletin = scrape_inhga.INHGABulletinSummary(
            bulletin_date="02.11.2026", bulletin_url="https://www.hidro.ro/bulletin/test/",
            forecast_table_img="https://www.hidro.ro/table-768x1024.jpg")
        self.records = inhga_ocr._parse_grid(synthetic_grid(), "02.11.2026")

    async def test_parallel_requests_extract_only_once_and_use_original_image(self):
        urls = []
        def handle(request):
            urls.append(str(request.url))
            return httpx.Response(200, content=b"image")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch.object(inhga_ocr, "parse_bulletin_image", return_value=self.records) as parse:
                results = await asyncio.gather(*(scrape_inhga._fetch_bulletin_forecast(client, self.bulletin) for _ in range(3)))
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(urls, ["https://www.hidro.ro/table.jpg"])
        self.assertEqual(len(results[0]["rows"]), 17)
        self.assertEqual(results[0]["format"], "table")

    async def test_new_bulletin_invalidates_cache(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"image"))) as client:
            with patch.object(inhga_ocr, "parse_bulletin_image", return_value=self.records) as parse:
                await scrape_inhga._fetch_bulletin_forecast(client, self.bulletin)
                newer = copy.copy(self.bulletin)
                newer.bulletin_date = "03.11.2026"
                await scrape_inhga._fetch_bulletin_forecast(client, newer)
                self.assertEqual(parse.call_count, 2)

    async def test_unreadable_image_does_not_return_cached_old_numbers(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"bad"))) as client:
            with patch.object(inhga_ocr, "parse_bulletin_image", side_effect=ValueError("unreadable")):
                with self.assertRaises(ValueError):
                    await scrape_inhga._fetch_bulletin_forecast(client, self.bulletin)
        self.assertIsNone(scrape_inhga._bulletin_forecast_cache)
