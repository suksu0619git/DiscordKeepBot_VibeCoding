"""`EventListener.add_schedule` 테스트.

`/일정` 모달과 신입 OT 수요조사가 같은 저장소를 쓰므로, 인터랙션 없이 호출되는
공용 진입점이 저장·조회·삭제까지 제대로 도는지만 확인한다.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KST = dt.timezone(dt.timedelta(hours=9))
TARGET = dt.datetime(2026, 9, 23, 21, 0, tzinfo=KST)


def make_cog():
    from cogs.event_listener import EventListener

    cog = EventListener.__new__(EventListener)
    cog.bot = MagicMock()
    cog.target_channel_id = None
    cog.db_file = os.path.join(tempfile.mkdtemp(), "schedules.json")
    cog.meeting_schedule = []
    cog.get_target_channel = AsyncMock(return_value=None)  # 공지는 생략
    return cog


class AddScheduleTest(unittest.IsolatedAsyncioTestCase):
    async def test_stores_and_finds_schedule(self):
        cog = make_cog()
        created = await cog.add_schedule(
            target_time=TARGET,
            content="신입 오리엔테이션",
            mention="<@222>",
            author="신입 OT 수요조사",
            schedule_id="ot-2026-09-23",
            kind="ot",
            participants=[222],
        )

        self.assertEqual(cog.meeting_schedule, [created])
        self.assertEqual(created["kind"], "ot")
        self.assertEqual(created["participants"], [222])
        self.assertFalse(created["notified_10m"])
        self.assertIs(cog.find_schedule("ot-2026-09-23"), created)

    async def test_saved_file_reloads_with_participants(self):
        cog = make_cog()
        await cog.add_schedule(
            target_time=TARGET,
            content="신입 오리엔테이션",
            schedule_id="ot-2026-09-23",
            kind="ot",
            participants=[222, 333],
        )

        reloaded = cog.load_schedules()
        self.assertEqual(len(reloaded), 1)
        self.assertEqual(reloaded[0]["participants"], [222, 333])
        self.assertEqual(reloaded[0]["time"], TARGET)

    async def test_remove_schedule(self):
        cog = make_cog()
        await cog.add_schedule(
            target_time=TARGET, content="신입 오리엔테이션", schedule_id="ot-2026-09-23"
        )
        self.assertIsNotNone(cog.remove_schedule("ot-2026-09-23"))
        self.assertEqual(cog.meeting_schedule, [])
        self.assertIsNone(cog.remove_schedule("ot-2026-09-23"))


if __name__ == "__main__":
    unittest.main()
