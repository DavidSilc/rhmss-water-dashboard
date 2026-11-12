import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import serbian_plants as plants


class SerbianPlantTests(unittest.TestCase):
    def test_total_uses_the_latest_common_timestamp_and_preserves_zero(self):
        data = [
            {"EntsoeCode": "34WEHR-BBASRG1-X", "xAxisValues": [1000, 2000], "data": [0, 20]},
            {"EntsoeCode": "34WEHR-BBASRG2-U", "data": [0, None]},
            {"EntsoeCode": "other-plant", "data": [1000, 2000]},
        ]
        result = plants.parse_generation(data)
        self.assertEqual(result["mw"], 0)
        self.assertEqual(result["at"], 1)
        self.assertEqual(len(result["units"]), 2)
        self.assertIsNone(plants.parse_generation(data[:1]))

    def test_missing_values_are_not_an_offline_unit(self):
        data = [
            {"EntsoeCode": "34WEHR-BBASRG1-X", "xAxisValues": [1000], "data": [None]},
            {"EntsoeCode": "34WEHR-BBASRG2-U", "data": [20]},
        ]
        self.assertIsNone(plants.parse_generation(data))

    def test_unit_notices_distinguish_conventional_and_pumped_storage(self):
        html = """<table class="otable"><thead><tr><th>Unit</th><th>Lost</th>
        <th>Period</th><th></th></tr></thead><tbody>
        <tr><td>HE BAJINA BASTA<span class="osub">Hydro · G2</span></td><td>105 MW</td>
        <td>10 Nov – 14 Nov</td><td>maintenance</td></tr>
        <tr><td>RHE BAJINA BASTA<span class="osub">Pumped · G1</span></td><td>307 MW</td>
        <td>12 Nov – 15 Nov</td><td>forced</td></tr></tbody></table>"""
        result = plants.parse_notices(html)["plants"]
        self.assertEqual(result["Bajina Bašta"][0]["lost"], "105 MW")
        self.assertEqual(result["RHE Bajina Bašta"][0]["lost"], "307 MW")
        self.assertIsNone(plants.parse_notices("<html>Access denied</html>"))


class SavedGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_sources_keep_dated_seed_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory) / "seed.json"
            cache = Path(directory) / "cache.json"
            value = {"generation": {"RHE Bajina Bašta": {"at": 100, "mw": 0}}, "notices": None}
            seed.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(plants, "SEED", seed), patch.object(plants, "CACHE_FILE", cache), \
                 patch.object(plants, "_data", None), patch.object(plants, "_checked", -1000), \
                 patch.object(plants, "_fetch", AsyncMock(return_value=None)):
                result = await plants.fetch_serbian_plants()
            self.assertEqual(result, value)
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["generation"], value["generation"])
