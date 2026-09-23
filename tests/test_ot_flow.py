"""신입 OT: 반응 → 일정 자동 등록 → 참가 처리 흐름 테스트.

디스코드 연결 없이 돌도록 공지 메시지 / EventListener 를 스텁으로 대체한다.
검증 대상은 "미참가자가 1명이라도 누르면 일정이 잡히고, 기존 참가자만 누르면
잡히지 않는다" 는 규칙과, 그 뒤 참가 처리로 다음 주에는 잡히지 않는다는 것이다.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from services.ot_db import OtAttendanceDB  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
NOTICE_DAY = dt.date(2026, 9, 23)  # 수요일
AFTERNOON = dt.datetime(2026, 9, 23, 15, 0, tzinfo=KST)
LATE_NIGHT = dt.datetime(2026, 9, 23, 23, 30, tzinfo=KST)
NEXT_DAY = dt.datetime(2026, 9, 24, 15, 0, tzinfo=KST)

NOTICE_MESSAGE_ID = 999

OLD_MEMBER = 111  # 기존 인원(참가함으로 시딩됨)
NEWBIE = 222  # 신입(DB 에 행이 없음 → 미참가)
EXCLUDED = 333  # 기존 인원이지만 OT 를 안 들어 예외로 지정된 사람


async def set_joining(db, user_ids: list[int]) -> None:
    """'참가' 를 누른 사람을 그대로 맞춘다(기존 응답은 지운다).

    버튼은 이모지와 달리 디스코드가 들고 있지 않으므로 DB 가 유일한 근거다.
    """
    for existing in await db.response_user_ids(NOTICE_MESSAGE_ID, True):
        await db.clear_response(NOTICE_MESSAGE_ID, existing)
    for user_id in user_ids:
        await db.set_response(NOTICE_MESSAGE_ID, user_id, True)


class FakeEventListener:
    """일정 저장소만 흉내 내는 EventListener 스텁."""

    def __init__(self):
        self.schedules: list[dict] = []
        self.saved = 0

    def find_schedule(self, schedule_id):
        for m in self.schedules:
            if str(m.get("id")) == str(schedule_id):
                return m
        return None

    async def add_schedule(self, **kwargs):
        schedule = {
            "id": kwargs["schedule_id"],
            "time": kwargs["target_time"],
            "content": kwargs["content"],
            "mention": kwargs["mention"],
            "kind": kwargs["kind"],
            "participants": list(kwargs["participants"]),
            "author": kwargs["author"],
        }
        self.schedules.append(schedule)
        return schedule

    def remove_schedule(self, schedule_id):
        found = self.find_schedule(schedule_id)
        if found is not None:
            self.schedules.remove(found)
        return found

    def save_schedules(self):
        self.saved += 1


async def make_cog(joining: list[int], now: dt.datetime, notice_date: dt.date = NOTICE_DAY):
    """OtNotice 를 __init__ 없이 만들고 DB·공지 메시지·EventListener 를 붙인다."""
    from cogs.ot_notice import OtNotice
    from services.ot_db import (
        META_NOTICE_CHANNEL_ID,
        META_NOTICE_DATE,
        META_NOTICE_MESSAGE_ID,
    )

    db = OtAttendanceDB(os.path.join(tempfile.mkdtemp(), "ot.db"))
    await db.init_schema()
    # 기존 인원 스캔: OLD_MEMBER 는 참가함, EXCLUDED 는 예외라 미참가. NEWBIE 는 아예 없음.
    await db.seed([OLD_MEMBER, EXCLUDED], [EXCLUDED])
    await db.set_meta(META_NOTICE_MESSAGE_ID, NOTICE_MESSAGE_ID)
    await db.set_meta(META_NOTICE_CHANNEL_ID, 888)
    await db.set_meta(META_NOTICE_DATE, notice_date.isoformat())

    import asyncio

    cog = OtNotice.__new__(OtNotice)
    cog.bot = MagicMock()
    cog.db = db
    cog._sync_lock = asyncio.Lock()
    cog._now = lambda: now

    await set_joining(db, joining)

    message = MagicMock()
    message.id = NOTICE_MESSAGE_ID

    async def fetch():
        return message

    cog._fetch_notice_message = fetch

    events = FakeEventListener()
    cog.bot.get_cog = lambda name: events if name == "EventListener" else None
    return cog, events, db


class ButtonToScheduleTest(unittest.IsolatedAsyncioTestCase):
    async def test_newbie_join_creates_schedule(self):
        cog, events, db = await make_cog([NEWBIE], AFTERNOON)
        await cog._sync_participants()

        self.assertEqual(len(events.schedules), 1)
        schedule = events.schedules[0]
        self.assertEqual(schedule["participants"], [NEWBIE])
        self.assertEqual(schedule["mention"], f"<@{NEWBIE}>")
        self.assertEqual(schedule["content"], config.OT_SCHEDULE_CONTENT)
        self.assertEqual(
            (schedule["time"].date(), schedule["time"].hour, schedule["time"].minute),
            (NOTICE_DAY, config.OT_SCHEDULE_HOUR, config.OT_SCHEDULE_MINUTE),
        )

    async def test_excluded_existing_member_also_triggers(self):
        """기존 인원이어도 예외로 지정돼 있으면(= OT 를 안 들었으면) 일정이 잡힌다."""
        cog, events, db = await make_cog([EXCLUDED], AFTERNOON)
        await cog._sync_participants()
        self.assertEqual(events.schedules[0]["participants"], [EXCLUDED])

    async def test_attended_member_alone_does_not_trigger(self):
        cog, events, db = await make_cog([OLD_MEMBER], AFTERNOON)
        await cog._sync_participants()
        self.assertEqual(events.schedules, [])

    async def test_everyone_who_joined_is_mentioned_not_just_pending_ones(self):
        """멘션 대상은 참가를 누른 사람 전원 — 트리거만 미참가자 기준이다."""
        cog, events, db = await make_cog([OLD_MEMBER, NEWBIE], AFTERNOON)
        await cog._sync_participants()

        schedule = events.schedules[0]
        self.assertEqual(schedule["participants"], [OLD_MEMBER, NEWBIE])
        self.assertEqual(schedule["mention"], f"<@{OLD_MEMBER}> <@{NEWBIE}>")

    async def test_attended_member_joining_later_is_added_to_mentions(self):
        cog, events, db = await make_cog([NEWBIE], AFTERNOON)
        await cog._sync_participants()
        await set_joining(db, [NEWBIE, OLD_MEMBER])
        await cog._sync_participants()

        self.assertEqual(len(events.schedules), 1)
        self.assertEqual(events.schedules[0]["participants"], [NEWBIE, OLD_MEMBER])

    async def test_schedule_removed_when_only_attended_members_remain(self):
        """미참가자가 응답을 취소하면, 기존 참가자가 남아 있어도 OT 는 열지 않는다."""
        cog, events, db = await make_cog([NEWBIE, OLD_MEMBER], AFTERNOON)
        await cog._sync_participants()
        await set_joining(db, [OLD_MEMBER])
        await cog._sync_participants()

        self.assertEqual(events.schedules, [])

    async def test_second_join_updates_instead_of_duplicating(self):
        cog, events, db = await make_cog([NEWBIE], AFTERNOON)
        await cog._sync_participants()
        # 두 번째 미참가자가 추가로 참가를 눌렀다.
        await set_joining(db, [NEWBIE, EXCLUDED])
        await cog._sync_participants()

        self.assertEqual(len(events.schedules), 1)
        self.assertEqual(events.schedules[0]["participants"], [NEWBIE, EXCLUDED])
        self.assertIn(f"<@{EXCLUDED}>", events.schedules[0]["mention"])
        self.assertEqual(events.saved, 1)

    async def test_schedule_removed_when_everyone_cancels(self):
        cog, events, db = await make_cog([NEWBIE], AFTERNOON)
        await cog._sync_participants()
        await set_joining(db, [])
        await cog._sync_participants()
        self.assertEqual(events.schedules, [])

    async def test_ignores_responses_after_the_notice_day(self):
        cog, events, db = await make_cog([NEWBIE], NEXT_DAY)
        await cog._sync_participants()
        self.assertEqual(events.schedules, [])

    async def test_does_not_create_schedule_in_the_past(self):
        cog, events, db = await make_cog([NEWBIE], LATE_NIGHT)
        await cog._sync_participants()
        self.assertEqual(events.schedules, [])


class AttendanceAfterOtTest(unittest.IsolatedAsyncioTestCase):
    async def test_participants_marked_attended_when_schedule_fires(self):
        cog, events, db = await make_cog([NEWBIE], AFTERNOON)
        await cog._sync_participants()
        await cog.on_schedule_fired(events.schedules[0])

        self.assertTrue(await db.is_attended(NEWBIE))

    async def test_next_week_does_not_trigger_for_the_same_person(self):
        cog, events, db = await make_cog([NEWBIE], AFTERNOON)
        await cog._sync_participants()
        await cog.on_schedule_fired(events.schedules[0])

        events.schedules.clear()
        await cog._sync_participants()  # 같은 사람이 또 눌러도
        self.assertEqual(events.schedules, [])  # 이제는 일정이 잡히지 않는다

    async def test_other_schedules_are_ignored(self):
        cog, _, db = await make_cog([NEWBIE], AFTERNOON)
        await cog.on_schedule_fired({"content": "회의", "participants": [NEWBIE]})
        self.assertFalse(await db.is_attended(NEWBIE))




if __name__ == "__main__":
    unittest.main()
