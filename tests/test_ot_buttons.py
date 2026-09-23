"""신입 OT 수요조사 참가/불참 버튼 단위 테스트.

이모지 반응과 달리 버튼은 **누른 사람을 디스코드가 들고 있지 않으므로** 응답을
`ot_responses` 테이블에 직접 저장한다. 그래서 여기서 확인할 것은 세 가지다.

1. 참가/불참이 DB 에 제대로 남는가 (같은 버튼을 다시 누르면 취소되는가)
2. 지난 주 공지의 버튼을 눌렀을 때 이번 주 일정을 건드리지 않는가
3. 임베드에 참가/불참 명단이 반영되는가
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from services.ot_db import META_NOTICE_MESSAGE_ID, OtAttendanceDB  # noqa: E402

NOTICE_ID = 999
OLD_NOTICE_ID = 111
USER = 4242


def make_interaction(message_id: int | None, user_id: int = USER):
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    if message_id is None:
        interaction.message = None
    else:
        interaction.message = MagicMock()
        interaction.message.id = message_id
    return interaction


async def make_cog():
    from cogs.ot_notice import OtNotice

    cog = OtNotice.__new__(OtNotice)
    cog.bot = MagicMock()
    cog.db = OtAttendanceDB(os.path.join(tempfile.mkdtemp(), "ot.db"))
    await cog.db.init_schema()
    await cog.db.set_meta(META_NOTICE_MESSAGE_ID, NOTICE_ID)
    cog.refresh_notice_embed = AsyncMock()
    cog._sync_participants = AsyncMock()
    return cog


class ButtonResponseTest(unittest.IsolatedAsyncioTestCase):
    async def test_join_is_recorded(self):
        cog = await make_cog()
        await cog.handle_button(make_interaction(NOTICE_ID), True)
        self.assertIs(await cog.db.get_response(NOTICE_ID, USER), True)
        cog._sync_participants.assert_awaited_once()

    async def test_decline_is_recorded(self):
        cog = await make_cog()
        await cog.handle_button(make_interaction(NOTICE_ID), False)
        self.assertIs(await cog.db.get_response(NOTICE_ID, USER), False)

    async def test_pressing_same_button_twice_cancels(self):
        cog = await make_cog()
        await cog.handle_button(make_interaction(NOTICE_ID), True)
        await cog.handle_button(make_interaction(NOTICE_ID), True)
        self.assertIsNone(await cog.db.get_response(NOTICE_ID, USER))

    async def test_switching_side_overwrites(self):
        cog = await make_cog()
        await cog.handle_button(make_interaction(NOTICE_ID), True)
        await cog.handle_button(make_interaction(NOTICE_ID), False)
        self.assertIs(await cog.db.get_response(NOTICE_ID, USER), False)

    async def test_old_notice_is_rejected(self):
        """지난 주 공지의 버튼으로 이번 주 일정을 건드리면 안 된다."""
        cog = await make_cog()
        interaction = make_interaction(OLD_NOTICE_ID)
        await cog.handle_button(interaction, True)

        self.assertIsNone(await cog.db.get_response(OLD_NOTICE_ID, USER))
        cog._sync_participants.assert_not_awaited()
        reply = interaction.response.send_message.await_args.args[0]
        self.assertIn("지난", reply)

    async def test_embed_failure_does_not_lose_the_response(self):
        import discord

        cog = await make_cog()
        cog.refresh_notice_embed = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "edit 실패")
        )
        await cog.handle_button(make_interaction(NOTICE_ID), True)
        # 임베드 갱신이 실패해도 응답과 일정 계산은 그대로여야 한다.
        self.assertIs(await cog.db.get_response(NOTICE_ID, USER), True)
        cog._sync_participants.assert_awaited_once()


class NoticeEmbedTest(unittest.IsolatedAsyncioTestCase):
    async def test_embed_lists_both_sides(self):
        cog = await make_cog()
        embed = cog.build_notice_embed([1, 2], [3])

        self.assertEqual(embed.description, config.OT_NOTICE_MESSAGE)
        self.assertIn("참가 2명", embed.fields[0].name)
        self.assertEqual(embed.fields[0].value, "<@1> <@2>")
        self.assertIn("불참 1명", embed.fields[1].name)
        self.assertEqual(embed.fields[1].value, "<@3>")

    async def test_empty_embed_uses_placeholder(self):
        cog = await make_cog()
        embed = cog.build_notice_embed([], [])
        self.assertEqual(embed.fields[0].value, "—")
        self.assertEqual(embed.fields[1].value, "—")


class ResponseOrderTest(unittest.IsolatedAsyncioTestCase):
    async def test_responses_keep_press_order(self):
        """명단 순서가 누를 때마다 뒤바뀌면 공지가 산만해진다."""
        cog = await make_cog()
        for user_id in (30, 10, 20):
            await cog.db.set_response(NOTICE_ID, user_id, True)
        self.assertEqual(
            await cog.db.response_user_ids(NOTICE_ID, True), [30, 10, 20]
        )

    async def test_sides_are_separated(self):
        cog = await make_cog()
        await cog.db.set_response(NOTICE_ID, 1, True)
        await cog.db.set_response(NOTICE_ID, 2, False)
        self.assertEqual(await cog.db.response_user_ids(NOTICE_ID, True), [1])
        self.assertEqual(await cog.db.response_user_ids(NOTICE_ID, False), [2])

    async def test_notices_do_not_mix(self):
        """공지별로 따로 남아야 지난 주 응답이 이번 주에 섞이지 않는다."""
        cog = await make_cog()
        await cog.db.set_response(OLD_NOTICE_ID, 1, True)
        await cog.db.set_response(NOTICE_ID, 2, True)
        self.assertEqual(await cog.db.response_user_ids(NOTICE_ID, True), [2])


if __name__ == "__main__":
    unittest.main()
