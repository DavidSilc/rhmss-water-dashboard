import unittest
from unittest.mock import patch

import httpx

from app import scrape_inhga as scraper


def xml_item(station="Tulcea", km="71", observed="2026-11-02", issued="2026-11-02",
             forecasts=("0", "-1", "2", "3", "4")):
    fields = {
        "field_localitatea": station, "field_km": km, "field_cota": "0",
        "field_variatia": "-2", "field_field_data_actualiz_cote": observed,
        "field_data_actualizare_prognoze": issued,
        **{f"field_tendinta_{h}h": v for h, v in zip((24, 48, 72, 96, 120), forecasts)},
    }
    return "<item>" + "".join(f"<{k}><value>{v}</value></{k}>" for k, v in fields.items()) + "</item>"


XML = "<response>" + xml_item() + "</response>"


class ParserTests(unittest.TestCase):
    def test_bulletin_uses_largest_image_even_when_srcset_is_unsorted(self):
        img = {"src": "small.jpg", "srcset": "large.jpg 768w, medium.jpg 640w, tiny.jpg 400w"}
        self.assertEqual(scraper._largest_image_source(img), "large.jpg")

    def test_zero_and_negative_forecasts_are_preserved(self):
        d = scraper._parse_afdj_xml(XML)
        self.assertEqual(d["rows"][0]["values"], ["0", "0", "-1", "2", "3", "4"])
        self.assertIn("03.11", d["columns"][3])

    def test_previous_issue_is_aligned_to_actual_dates(self):
        d = scraper._parse_afdj_xml("<response>" + xml_item(issued="2026-11-01") + "</response>")
        self.assertEqual(d["rows"][0]["values"], ["0", "-1", "2", "3", "4", ""])
        self.assertTrue(d["warning"])
        self.assertNotIn("today", " ".join(d["columns"]))

    def test_missing_station_forecasts_are_not_reported_as_zero(self):
        xml = "<response>" + xml_item() + xml_item("Sulina", "0", forecasts=("0",) * 5) + "</response>"
        rows = scraper._parse_afdj_xml(xml)["rows"]
        self.assertEqual(rows[0]["station"], "Sulina")
        self.assertEqual(rows[0]["values"], ["0", "", "", "", "", ""])

    def test_invalid_or_expired_feed_is_rejected(self):
        for xml in ("<response/>", "<html>challenge</html>",
                    "<response>" + xml_item(issued="2026-10-01") + "</response>",
                    "<response>" + xml_item(observed="unknown") + "</response>"):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                scraper._parse_afdj_xml(xml)

    def test_html_uses_forecast_issue_date(self):
        cells = ["Tulcea", "71", "8 cm", "3", "14 C", "02/11/2026",
                 "3", "5", "6", "7", "8", "01/11/2026"]
        html = "<table><tr><th>Station</th></tr><tr>" + "".join(f"<td>{v}</td>" for v in cells) + "</tr></table>"
        d = scraper._parse_afdj_table(html)
        self.assertEqual(d["rows"][0]["values"], ["8", "5", "6", "7", "8", ""])


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.object(scraper.httpx, "AsyncClient", return_value=client):
            return await scraper.fetch_danube_forecast()

    async def test_html_403_falls_back_to_official_xml(self):
        def handler(request):
            return httpx.Response(200, text=XML) if str(request.url) == scraper.AFDJ_XML_URL else httpx.Response(403)
        d = await self.fetch(handler)
        self.assertIsNone(d["error"])
        self.assertEqual(d["source_url"], scraper.AFDJ_XML_URL)
        self.assertEqual(len(d["rows"]), 1)

    async def test_html_challenge_with_200_also_falls_back(self):
        def handler(request):
            return httpx.Response(200, text=XML if str(request.url) == scraper.AFDJ_XML_URL else "<html>Challenge</html>")
        self.assertEqual((await self.fetch(handler))["format"], "table")

    async def test_both_afdj_routes_blocked_uses_inhga_bulletin(self):
        bulletin = scraper.INHGABulletinSummary(
            bulletin_date="02.11.2026", bulletin_url="https://www.hidro.ro/bulletin/test/",
            forecast_table_img="https://www.hidro.ro/forecast.jpg")
        with patch.object(scraper, "_fetch_bulletin_summary", return_value=bulletin):
            d = await self.fetch(lambda request: httpx.Response(403))
        self.assertIsNone(d["error"])
        self.assertEqual(d["format"], "bulletin")
        self.assertEqual(d["rows"], [])
        self.assertEqual(d["bulletin"]["bulletin_date"], "02.11.2026")

    async def test_all_sources_unavailable_returns_honest_error(self):
        with patch.object(scraper, "_fetch_bulletin_summary", return_value=scraper.INHGABulletinSummary()):
            d = await self.fetch(lambda request: httpx.Response(403))
        self.assertTrue(d["error"])
        self.assertEqual(d["rows"], [])


if __name__ == "__main__":
    unittest.main()
