"""신입 OT 수요조사 공지 단위 테스트.

`tasks.loop(time=...)` 은 매일 깨어나므로, "수요일에만 보낸다"는 판단은 루프 안의
코드가 책임진다. 그 판단과 중복 발송 방지가 여기서 검증하는 전부다.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
WEDNESDAY_2PM = dt.datetime(2026, 9, 23, 14, 0, tzinfo=KST)
THURSDAY_2PM = dt.datetime(2026, 9, 24, 14, 0, tzinfo=KST)


def make_cog(now: dt.datetime):
    """OtNotice 를 __init__(루프 시작) 없이 만들고 시각만 고정한다."""
    from cogs.ot_notice import OtNotice

    cog = OtNotice.__new__(OtNotice)
    cog.bot = MagicMock()
    cog._last_sent_date = None
    cog._now = lambda: now
    cog.send_notice = AsyncMock(return_value=MagicMock(id=1))
    return cog


class WeeklyNoticeTest(unittest.IsolatedAsyncioTestCase):
    async def test_sends_on_configured_weekday(self):
        cog = make_cog(WEDNESDAY_2PM)
        await cog.weekly_notice()
        cog.send_notice.assert_awaited_once()

    async def test_skips_other_weekdays(self):
        cog = make_cog(THURSDAY_2PM)
        await cog.weekly_notice()
        cog.send_notice.assert_not_awaited()

    async def test_does_not_send_twice_on_same_day(self):
        """리로드로 루프가 다시 시작돼도 같은 날 두 번 올라가면 안 된다."""
        cog = make_cog(WEDNESDAY_2PM)
        await cog.weekly_notice()
        await cog.weekly_notice()
        self.assertEqual(cog.send_notice.await_count, 1)

    async def test_loop_survives_send_failure(self):
        cog = make_cog(WEDNESDAY_2PM)
        cog.send_notice = AsyncMock(side_effect=RuntimeError("보내기 실패"))
        await cog.weekly_notice()  # 예외가 새어나가면 루프가 죽는다
        self.assertIsNone(cog._last_sent_date)  # 실패했으면 다음 기회에 다시 시도


class SendNoticeTest(unittest.IsolatedAsyncioTestCase):
    async def test_posts_message_prereacts_and_remembers_the_message(self):
        import os
        import tempfile

        from cogs.ot_notice import OtNotice
        from services.ot_db import META_NOTICE_DATE, META_NOTICE_MESSAGE_ID, OtAttendanceDB

        cog = OtNotice.__new__(OtNotice)
        cog.bot = MagicMock()
        cog.db = OtAttendanceDB(os.path.join(tempfile.mkdtemp(), "ot.db"))
        await cog.db.init_schema()
        cog._now = lambda: WEDNESDAY_2PM

        message = MagicMock()
        message.id = 4242
        message.channel.id = 777
        message.add_reaction = AsyncMock()
        channel = MagicMock()
        channel.send = AsyncMock(return_value=message)
        cog._get_channel = AsyncMock(return_value=channel)

        sent = await cog.send_notice()

        self.assertIs(sent, message)
        self.assertEqual(channel.send.await_args.args[0], config.OT_NOTICE_MESSAGE)
        message.add_reaction.assert_awaited_once_with(config.OT_NOTICE_EMOJI)
        # 반응을 감시할 대상으로 이 메시지를 기억해야 한다.
        self.assertEqual(await cog.db.get_meta_int(META_NOTICE_MESSAGE_ID), 4242)
        self.assertEqual(
            await cog.db.get_meta(META_NOTICE_DATE), WEDNESDAY_2PM.date().isoformat()
        )


class ConfigDefaultsTest(unittest.TestCase):
    def test_defaults_point_at_wednesday_2pm(self):
        self.assertEqual(config.OT_NOTICE_WEEKDAY, 2)  # 0=월 → 2=수
        self.assertEqual((config.OT_NOTICE_HOUR, config.OT_NOTICE_MINUTE), (14, 0))
        self.assertEqual(config.OT_NOTICE_CHANNEL_ID, 1422581802479910994)

    def test_message_is_two_lines_with_emoji(self):
        lines = config.OT_NOTICE_MESSAGE.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("# "))
        self.assertTrue(lines[1].startswith("## "))
        self.assertIn(config.OT_NOTICE_EMOJI, lines[1])


if __name__ == "__main__":
    unittest.main()
