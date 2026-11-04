import unittest

import httpx

from app.romanian_plants import latest_reading, operator_reports, unit_generation, unit_notices


class RomanianPlantTests(unittest.TestCase):
    def test_measurement_ignores_missing_values_but_keeps_zero(self):
        data = {"ok": True, "puncte": [[1, 45], [2, 0], [3, None]]}
        self.assertEqual(latest_reading(httpx.Response(200, json=data)), {"mw": 0, "at": 2})

    def test_unit_readings_keep_individual_dates_and_exclude_totals(self):
        series = [
            {"EntsoeCode": "30WCERNCERN1---Y", "xAxisValues": [1000, 2000], "data": [0, None]},
            {"EntsoeCode": "30WCERNCERN2---T", "data": [600, 610]},
            {"data": [600, 610]},
        ]
        self.assertEqual(unit_generation(httpx.Response(200, json=series)), {
            "1": {"at": 1, "mw": 0}, "2": {"at": 2, "mw": 610}})

    def test_active_notice_uses_latest_revision_and_correct_unit(self):
        base = {"plant": "NPP Cernavoda", "machine": "CNE__U2_RET_CA", "acum": True,
                "from": 1, "to": 8, "at": 2, "lost": 650}
        rows = [base, {**base, "at": 3, "to": 10}, {**base, "acum": False, "at": 4}]
        result = unit_notices(httpx.Response(200, json={"ok": True, "opriri": rows}))
        self.assertEqual(list(result["units"]), ["2"])
        self.assertEqual(result["units"]["2"]["to"], 10)

    def test_new_operator_report_supersedes_only_named_unit(self):
        feed = b"""<rss><channel><item><title>Reconnection of Unit 1</title>
        <link>https://nuclearelectrica.ro/snn/en/reconnection/</link>
        <pubDate>Fri, 06 Nov 2026 12:00:00 +0000</pubDate></item></channel></rss>"""
        reports = operator_reports(httpx.Response(200, content=feed))
        self.assertEqual(reports["1"]["date"], "2026-11-06")
        self.assertEqual(reports["2"]["date"], "2026-11-04")
