"""FR-6 반응 감지 단위 테스트.

핵심 회귀 방지 대상: 포럼 포스트/댓글의 `payload.channel_id` 는 **스레드 ID** 이므로
`SOURCE_CHANNEL_IDS` 에 포럼 채널 ID만 있어도 감지되어야 한다.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FORUM_ID = 1530277835640410145
THREAD_ID = 1530277900000000000
PLAIN_TEXT_CHANNEL_ID = 1422581802479910994
UNWATCHED_ID = 999999999999999999


def FakeThread(thread_id: int, parent_id: int):
    """`isinstance(x, discord.Thread)` 를 만족하는 스텁(spec 지정 Mock)."""
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.parent_id = parent_id
    return thread


def FakeTextChannel(channel_id: int):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    return channel


def make_cog(source_ids: set[int]):
    """ReactionForward 를 __init__ 없이 만들어 필요한 속성만 채운다."""
    from cogs.reaction_forward import ReactionForward

    cog = ReactionForward.__new__(ReactionForward)
    cog.source_channel_ids = frozenset(source_ids)
    cog._parent_cache = {}
    cog.forwarded_messages = set()
    cog.super_announced = set()
    cog.target_channel_id = 1525109127775518780
    return cog


class ThreadParentMatchingTest(unittest.IsolatedAsyncioTestCase):
    async def test_forum_thread_matches_via_parent_channel(self):
        """이 테스트가 FR-6 의 실제 버그를 잡는다."""
        cog = make_cog({FORUM_ID})
        cog._get_channel = AsyncMock(return_value=FakeThread(THREAD_ID, FORUM_ID))
        self.assertTrue(await cog._is_watched_channel(THREAD_ID))

    async def test_direct_channel_id_still_matches_without_fetch(self):
        cog = make_cog({PLAIN_TEXT_CHANNEL_ID})
        cog._get_channel = AsyncMock(side_effect=AssertionError("fetch 하지 않아야 함"))
        self.assertTrue(await cog._is_watched_channel(PLAIN_TEXT_CHANNEL_ID))

    async def test_thread_of_unwatched_parent_is_ignored(self):
        cog = make_cog({FORUM_ID})
        cog._get_channel = AsyncMock(return_value=FakeThread(THREAD_ID, UNWATCHED_ID))
        self.assertFalse(await cog._is_watched_channel(THREAD_ID))

    async def test_non_thread_channel_not_in_list_is_ignored(self):
        cog = make_cog({FORUM_ID})
        cog._get_channel = AsyncMock(return_value=FakeTextChannel(UNWATCHED_ID))
        self.assertFalse(await cog._is_watched_channel(UNWATCHED_ID))

    async def test_empty_source_list_watches_everything(self):
        cog = make_cog(set())
        cog._get_channel = AsyncMock(side_effect=AssertionError("fetch 하지 않아야 함"))
        self.assertTrue(await cog._is_watched_channel(UNWATCHED_ID))
        self.assertTrue(await cog._is_watched_channel(THREAD_ID))

    async def test_unresolvable_channel_is_ignored_without_crash(self):
        cog = make_cog({FORUM_ID})
        cog._get_channel = AsyncMock(return_value=None)  # 삭제된 채널 등
        self.assertFalse(await cog._is_watched_channel(THREAD_ID))

    async def test_parent_lookup_is_cached(self):
        """rate limit 절약: 같은 스레드는 한 번만 조회한다."""
        cog = make_cog({FORUM_ID})
        cog._get_channel = AsyncMock(return_value=FakeThread(THREAD_ID, FORUM_ID))
        for _ in range(5):
            self.assertTrue(await cog._is_watched_channel(THREAD_ID))
        self.assertEqual(cog._get_channel.await_count, 1)

    async def test_negative_result_is_also_cached(self):
        cog = make_cog({FORUM_ID})
        cog._get_channel = AsyncMock(return_value=FakeTextChannel(UNWATCHED_ID))
        for _ in range(3):
            await cog._is_watched_channel(UNWATCHED_ID)
        self.assertEqual(cog._get_channel.await_count, 1)


class ReactionCountTest(unittest.TestCase):
    def setUp(self):
        from cogs.reaction_forward import PIN_EMOJI, ReactionForward

        self.count = ReactionForward._count_reactions
        self.pin = PIN_EMOJI

    def _message(self, reactions):
        class FakeReaction:
            def __init__(self, emoji, count):
                self.emoji = emoji
                self.count = count

        class FakeMessage:
            def __init__(self, items):
                self.reactions = [FakeReaction(*item) for item in items]

        return FakeMessage(reactions)

    def test_counts_matching_emoji(self):
        self.assertEqual(self.count(self._message([(self.pin, 7)])), 7)

    def test_ignores_other_emojis(self):
        self.assertEqual(self.count(self._message([("🔥", 99), (self.pin, 3)])), 3)

    def test_no_matching_emoji_is_zero(self):
        self.assertEqual(self.count(self._message([("🔥", 99)])), 0)

    def test_no_reactions_is_zero(self):
        self.assertEqual(self.count(self._message([])), 0)


class DeanonymizeGuardTest(unittest.TestCase):
    """익명 채널의 실명 원본이 킾추로 새어나가지 않는지 확인한다."""

    ANON_ID = 1480391682817130719

    def setUp(self):
        import config
        from cogs.reaction_forward import ReactionForward

        self.config = config
        self.guard = ReactionForward._would_deanonymize
        self._original = config.ANONYMOUS_CHANNEL_IDS
        config.ANONYMOUS_CHANNEL_IDS = frozenset({self.ANON_ID})

    def tearDown(self):
        self.config.ANONYMOUS_CHANNEL_IDS = self._original

    def _message(self, channel_id: int, webhook_id):
        message = MagicMock()
        message.channel.id = channel_id
        message.webhook_id = webhook_id
        return message

    def test_anonymous_webhook_message_is_forwarded(self):
        # 익명 웹훅이 올린 글 → 작성자가 '익명(x.x.***.***)' 이므로 포워딩해도 안전하다.
        self.assertFalse(self.guard(self._message(self.ANON_ID, 555)))

    def test_real_name_message_in_anonymous_channel_is_blocked(self):
        # 원본 삭제가 실패해 실명으로 남은 글 → 포워딩하면 신원이 드러난다.
        self.assertTrue(self.guard(self._message(self.ANON_ID, None)))

    def test_normal_channel_is_unaffected(self):
        self.assertFalse(self.guard(self._message(PLAIN_TEXT_CHANNEL_ID, None)))


class OwnReactionTest(unittest.TestCase):
    def setUp(self):
        from cogs.reaction_forward import ReactionForward

        self.cog = ReactionForward.__new__(ReactionForward)

    def _payload(self, user_id):
        class FakePayload:
            def __init__(self, uid):
                self.user_id = uid

        return FakePayload(user_id)

    def test_bot_own_reaction_is_detected(self):
        class FakeBot:
            class user:
                id = 555

        self.cog.bot = FakeBot()
        self.assertTrue(self.cog._is_own_reaction(self._payload(555)))
        self.assertFalse(self.cog._is_own_reaction(self._payload(777)))

    def test_no_bot_user_yet_does_not_crash(self):
        class FakeBot:
            user = None

        self.cog.bot = FakeBot()
        self.assertFalse(self.cog._is_own_reaction(self._payload(555)))


class RawListenerRegistrationTest(unittest.TestCase):
    """캐시 기반 이벤트를 쓰지 않는지 구조적으로 확인한다."""

    def test_raw_listeners_are_registered(self):
        from cogs.reaction_forward import ReactionForward

        self.assertTrue(hasattr(ReactionForward, "on_raw_reaction_add"))
        self.assertTrue(hasattr(ReactionForward, "on_raw_reaction_remove"))

    def test_cache_based_listeners_are_absent(self):
        from cogs.reaction_forward import ReactionForward

        self.assertFalse(hasattr(ReactionForward, "on_reaction_add"))
        self.assertFalse(hasattr(ReactionForward, "on_reaction_remove"))

    def test_source_file_has_no_print_calls(self):
        import cogs.reaction_forward as module

        with open(module.__file__, "r", encoding="utf-8") as file:
            source = file.read()
        self.assertNotIn("print(", source)


if __name__ == "__main__":
    unittest.main()
