import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.romanian_cache import RomanianCache, read_snapshot, usable, write_snapshot


def snapshot(day="2026-11-09", level=0):
    return {"saved": 1794220000000, "data": {"stations": [
        {"name": "Cernavoda", "water_stage_cm": level, "date": day}], "error": None}}


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)
        self.seed = self.path / "seed"
        self.disk = self.path / "disk"
        self.cache = RomanianCache(self.disk, self.seed)
        self.cache._mirror = AsyncMock(return_value=None)

    async def asyncTearDown(self):
        await asyncio.gather(*self.cache.tasks.values())
        self.folder.cleanup()

    async def test_new_browser_and_cold_server_keep_seed_when_all_sources_are_blocked(self):
        write_snapshot(self.seed / "stations.json", snapshot())
        fetch = AsyncMock(side_effect=RuntimeError("403"))
        result = await self.cache.get("stations", fetch)
        await self.cache.tasks["stations"]
        self.assertEqual(result["stations"][0]["water_stage_cm"], 0)
        self.assertTrue(result["stale"])
        self.assertEqual(result["data_date"], "2026-11-09")
        self.assertIsNotNone(read_snapshot(self.disk / "stations.json", "stations"))

    async def test_success_survives_new_process_and_cache_older_than_one_day(self):
        fetch = AsyncMock(return_value=snapshot()["data"])
        await self.cache.get("stations", fetch)
        restarted = RomanianCache(self.disk, self.seed)
        restarted._mirror = AsyncMock(return_value=None)
        with patch("app.romanian_cache.time.time", return_value=time.time() + 7 * 86400):
            result = await restarted.get("stations", AsyncMock(side_effect=TimeoutError()))
            await restarted.tasks["stations"]
        self.assertEqual(result["data_date"], "2026-11-09")
        self.assertTrue(result["stale"])

    async def test_older_upstream_cannot_replace_newer_independent_snapshot(self):
        self.cache._mirror = AsyncMock(return_value=snapshot("2026-11-09", 12))
        result = await self.cache.get("stations", AsyncMock(return_value=snapshot("2026-11-08", 3)["data"]))
        self.assertEqual(result["stations"][0]["water_stage_cm"], 12)
        self.assertTrue(result["stale"])

    async def test_simultaneous_requests_share_one_refresh(self):
        write_snapshot(self.seed / "stations.json", snapshot())
        fetch = AsyncMock(return_value=snapshot()["data"])
        await asyncio.gather(*(self.cache.get("stations", fetch) for _ in range(10)))
        await self.cache.tasks["stations"]
        fetch.assert_awaited_once()

    async def test_corrupt_disk_and_empty_response_preserve_seed(self):
        write_snapshot(self.seed / "stations.json", snapshot())
        self.disk.mkdir()
        (self.disk / "stations.json").write_text("{broken", encoding="utf-8")
        result = await self.cache.get("stations", AsyncMock(return_value={"stations": []}))
        await self.cache.tasks["stations"]
        self.assertEqual(result["data_date"], "2026-11-09")
        self.assertFalse(usable("rows", {"rows": [{"values": ["1", None, ""]}], "columns": list("abcdef")}))
        self.assertTrue(usable("rows", {"rows": [{"values": ["1", "0", "-1"]}], "columns": list("abcdef")}))
