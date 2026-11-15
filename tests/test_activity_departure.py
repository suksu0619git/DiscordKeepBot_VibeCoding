import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.activity import ActivityTracker


class DepartureTest(unittest.IsolatedAsyncioTestCase):
    async def test_departure_removes_activity_record(self):
        cog = ActivityTracker.__new__(ActivityTracker)
        cog.bot = SimpleNamespace(guilds=[SimpleNamespace(id=1)])
        cog.db = SimpleNamespace(delete=AsyncMock(return_value=True))
        cog._ensure_schema = AsyncMock()
        await cog.on_member_remove(SimpleNamespace(id=42, guild=SimpleNamespace(id=1)))
        cog.db.delete.assert_awaited_once_with(42)

    async def test_member_in_another_guild_is_preserved(self):
        cog = ActivityTracker.__new__(ActivityTracker)
        cog.bot = SimpleNamespace(guilds=[SimpleNamespace(id=2, fetch_member=AsyncMock())])
        cog.db = SimpleNamespace(delete=AsyncMock())
        await cog.on_member_remove(SimpleNamespace(id=42, guild=SimpleNamespace(id=1)))
        cog.db.delete.assert_not_awaited()

    async def test_failed_member_list_never_deletes_records(self):
        def unavailable(**kwargs):
            raise RuntimeError("member list unavailable")

        cog = ActivityTracker.__new__(ActivityTracker)
        cog.bot = SimpleNamespace(guilds=[SimpleNamespace(fetch_members=unavailable)])
        cog.db = SimpleNamespace(delete=AsyncMock())
        await cog.on_ready()
        cog.db.delete.assert_not_awaited()
