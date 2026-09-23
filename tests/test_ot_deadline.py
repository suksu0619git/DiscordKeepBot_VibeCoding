"""신입 OT 마감(19:00) 공지 단위 테스트.

마감은 '그 시점까지 모인 반응' 으로 오늘 OT 를 여는지 한 번 알리는 것이 전부다.
마감 뒤에 눌린 반응도 21시 일정에는 계속 반영되므로, 여기서 검증하는 건
**공지 문구 선택과 중복 발송 방지**다.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.datetime(2026, 9, 23, 19, 0, tzinfo=KST)

NEWBIE = 111  # 아직 OT 를 안 들은 사람
VETERAN = 222  # 이미 들은 사람


def make_cog(now: dt.datetime, reacted: list[int], attended: set[int], notice_date=None):
    from cogs.ot_notice import OtNotice

    cog = OtNotice.__new__(OtNotice)
    cog.bot = MagicMock()
    cog._last_deadline_date = None
    cog._now = lambda: now
    cog.db = MagicMock()
    cog.db.get_meta = AsyncMock(
        return_value=(notice_date or now.date()).isoformat()
    )
    cog.db.attended_user_ids = AsyncMock(return_value=attended)
    cog._fetch_notice_message = AsyncMock(return_value=MagicMock())
    cog._reacted_user_ids = AsyncMock(return_value=reacted)
    return cog


class DeadlineSummaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_newbie_reacted_means_open(self):
        cog = make_cog(TODAY, [NEWBIE, VETERAN], {VETERAN})
        will_open, participants = await cog.deadline_summary()
        self.assertTrue(will_open)
        self.assertEqual(participants, [NEWBIE, VETERAN])

    async def test_only_veterans_means_closed(self):
        """이미 들은 사람만 눌렀으면 OT 를 열지 않는다(일정 등록 규칙과 동일)."""
        cog = make_cog(TODAY, [VETERAN], {VETERAN})
        will_open, participants = await cog.deadline_summary()
        self.assertFalse(will_open)
        self.assertEqual(participants, [VETERAN])

    async def test_no_reaction_means_closed(self):
        cog = make_cog(TODAY, [], {VETERAN})
        will_open, _ = await cog.deadline_summary()
        self.assertFalse(will_open)

    async def test_no_notice_today_returns_none(self):
        """수요조사가 없던 날엔 아무 공지도 하지 않는다."""
        cog = make_cog(TODAY, [NEWBIE], set(), notice_date=dt.date(2026, 9, 16))
        self.assertIsNone(await cog.deadline_summary())


class DeadlineTextTest(unittest.TestCase):
    def setUp(self):
        from cogs.ot_notice import OtNotice

        self.text = OtNotice._deadline_text.__get__(MagicMock())

    def test_open_text_has_time_and_place(self):
        rendered = self.text(True, [NEWBIE])
        self.assertIn(f"{config.OT_SCHEDULE_HOUR}시", rendered)
        self.assertIn(config.OT_DEADLINE_PLACE, rendered)
        self.assertIn(f"<@{NEWBIE}>", rendered)
        self.assertNotIn("{", rendered)  # 포맷 자리표시자가 남으면 안 된다

    def test_closed_text_has_no_mentions(self):
        rendered = self.text(False, [NEWBIE, VETERAN])
        self.assertEqual(rendered, config.OT_DEADLINE_CLOSED_MESSAGE)
        self.assertNotIn("<@", rendered)


class DeadlineLoopTest(unittest.IsolatedAsyncioTestCase):
    def _cog(self):
        cog = make_cog(TODAY, [NEWBIE], set())
        cog.send_deadline_notice = AsyncMock(return_value=MagicMock())
        return cog

    async def test_sends_once_per_day(self):
        cog = self._cog()
        await cog.deadline_notice()
        await cog.deadline_notice()
        self.assertEqual(cog.send_deadline_notice.await_count, 1)

    async def test_no_notice_day_does_not_mark_sent(self):
        """공지할 게 없던 날은 발송으로 치지 않는다(다음 날 정상 동작해야 한다)."""
        cog = self._cog()
        cog.send_deadline_notice = AsyncMock(return_value=None)
        await cog.deadline_notice()
        self.assertIsNone(cog._last_deadline_date)

    async def test_loop_survives_failure(self):
        cog = self._cog()
        cog.send_deadline_notice = AsyncMock(side_effect=RuntimeError("보내기 실패"))
        await cog.deadline_notice()  # 예외가 새면 루프가 죽는다
        self.assertIsNone(cog._last_deadline_date)


if __name__ == "__main__":
    unittest.main()
