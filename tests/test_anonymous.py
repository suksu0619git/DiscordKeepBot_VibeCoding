"""FR-5 익명 채널 이모지 처리 단위 테스트.

핵심 회귀 방지 대상
1. 익명 채널에서 `EmojiExpander` 가 동작하면 실명이 노출된다 → 반드시 건너뛰어야 함
2. `delete()` 는 웹훅 재전송보다 **먼저** 실행되어야 함
3. `delete()` 실패는 삼키지 않고 Forbidden / NotFound 를 구분해 처리해야 함
"""

from __future__ import annotations

import logging
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from cogs.anonymous import AnonymousChat, emoji_image_url  # noqa: E402

ANON_CHANNEL_ID = 1530277805722571024
OTHER_CHANNEL_ID = 1422581802479910994


class EmojiImageUrlTest(unittest.TestCase):
    def test_static_custom_emoji(self):
        url = emoji_image_url("<:kipfel:123456789>")
        self.assertEqual(url, "https://cdn.discordapp.com/emojis/123456789.png?size=4096")

    def test_animated_custom_emoji_uses_gif(self):
        url = emoji_image_url("<a:dance:987654321>")
        self.assertEqual(url, "https://cdn.discordapp.com/emojis/987654321.gif?size=4096")

    def test_surrounding_whitespace_is_ignored(self):
        self.assertIsNotNone(emoji_image_url("  <:kipfel:123>  "))

    def test_text_with_emoji_is_not_expanded(self):
        # 텍스트 + 이모지 혼합은 원문 그대로 익명 전송되어야 한다.
        self.assertIsNone(emoji_image_url("안녕 <:kipfel:123>"))

    def test_two_emojis_are_not_expanded(self):
        self.assertIsNone(emoji_image_url("<:a:1><:b:2>"))

    def test_unicode_emoji_is_not_expanded(self):
        # 유니코드 이모지는 디스코드가 자체적으로 크게 렌더링한다.
        self.assertIsNone(emoji_image_url("😀"))

    def test_plain_text_and_empty(self):
        self.assertIsNone(emoji_image_url("hello"))
        self.assertIsNone(emoji_image_url(""))
        self.assertIsNone(emoji_image_url(None))


class AnonIdTest(unittest.TestCase):
    def setUp(self):
        self.cog = AnonymousChat.__new__(AnonymousChat)
        self.cog.user_salts = {}

    def test_format_is_masked_ip(self):
        self.assertRegex(self.cog.get_anon_id(123), r"^\d{1,3}\.\d{1,3}\.\*\*\*\.\*\*\*$")

    def test_same_user_is_stable(self):
        self.assertEqual(self.cog.get_anon_id(123), self.cog.get_anon_id(123))

    def test_salt_change_produces_new_id(self):
        before = self.cog.get_anon_id(123)
        self.cog.user_salts[123] = 1
        self.assertNotEqual(before, self.cog.get_anon_id(123))


def make_anon_cog() -> AnonymousChat:
    cog = AnonymousChat.__new__(AnonymousChat)
    cog.bot = MagicMock()
    cog.anon_channel_ids = frozenset({ANON_CHANNEL_ID})
    cog.user_salts = {}
    cog._permission_warned = False
    return cog


def make_message(content: str = "hello", channel_id: int = ANON_CHANNEL_ID):
    message = MagicMock(spec=discord.Message)
    message.id = 42
    message.content = content
    message.attachments = []
    message.author = MagicMock(spec=discord.Member)
    message.author.bot = False
    message.author.id = 777
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = "익명채널"
    channel.guild = MagicMock()
    channel.permissions_for.return_value = discord.Permissions.all()
    message.channel = channel
    message.delete = AsyncMock()
    return message


class DeleteBeforeResendTest(unittest.IsolatedAsyncioTestCase):
    """삭제 → 재전송 순서가 코드로 보장되는지 확인한다."""

    async def test_delete_happens_before_webhook_send(self):
        cog = make_anon_cog()
        order: list[str] = []
        message = make_message("<:kipfel:123>")
        message.delete = AsyncMock(side_effect=lambda: order.append("delete"))
        cog._send_anonymously = AsyncMock(side_effect=lambda *a, **k: order.append("send"))

        await cog.on_message(message)
        self.assertEqual(order, ["delete", "send"])

    async def test_forbidden_delete_aborts_resend(self):
        """삭제 권한이 없으면 익명 전송을 하지 않는다(중복 노출 방지)."""
        cog = make_anon_cog()
        message = make_message("<:kipfel:123>")
        message.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
        cog._send_anonymously = AsyncMock()

        with self.assertLogs("cogs.anonymous", level=logging.ERROR) as captured:
            await cog.on_message(message)
        cog._send_anonymously.assert_not_awaited()
        self.assertIn("권한 없음", "".join(captured.output))
        self.assertIn("메시지 관리", "".join(captured.output))

    async def test_not_found_delete_still_resends(self):
        """이미 삭제된 경우엔 원본이 없으므로 익명 전송을 계속한다."""
        cog = make_anon_cog()
        message = make_message("<:kipfel:123>")
        message.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
        cog._send_anonymously = AsyncMock()

        with self.assertLogs("cogs.anonymous", level=logging.WARNING):
            await cog.on_message(message)
        cog._send_anonymously.assert_awaited_once()

    async def test_delete_failure_is_not_silently_swallowed(self):
        cog = make_anon_cog()
        message = make_message("hi")
        message.delete = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500), "boom")
        )
        cog._send_anonymously = AsyncMock()

        with self.assertLogs("cogs.anonymous", level=logging.ERROR):
            await cog.on_message(message)
        cog._send_anonymously.assert_not_awaited()

    async def test_bot_messages_are_ignored(self):
        cog = make_anon_cog()
        message = make_message("hi")
        message.author.bot = True
        cog._send_anonymously = AsyncMock()
        await cog.on_message(message)
        message.delete.assert_not_awaited()
        cog._send_anonymously.assert_not_awaited()

    async def test_other_channels_are_ignored(self):
        cog = make_anon_cog()
        message = make_message("hi", channel_id=OTHER_CHANNEL_ID)
        cog._send_anonymously = AsyncMock()
        await cog.on_message(message)
        message.delete.assert_not_awaited()

    async def test_empty_message_without_files_is_ignored(self):
        cog = make_anon_cog()
        message = make_message("")
        cog._send_anonymously = AsyncMock()
        await cog.on_message(message)
        message.delete.assert_not_awaited()

    async def test_missing_manage_messages_permission_warns(self):
        cog = make_anon_cog()
        message = make_message("hi")
        perms = discord.Permissions.all()
        perms.manage_messages = False
        message.channel.permissions_for.return_value = perms
        cog._send_anonymously = AsyncMock()

        with self.assertLogs("cogs.anonymous", level=logging.WARNING) as captured:
            await cog.on_message(message)
        self.assertIn("메시지 관리", "".join(captured.output))


class AnonymousSendTest(unittest.IsolatedAsyncioTestCase):
    async def test_emoji_only_message_is_sent_as_cdn_url_with_anon_name(self):
        cog = make_anon_cog()
        webhook = AsyncMock()
        cog._get_webhook = AsyncMock(return_value=webhook)
        author = MagicMock(spec=discord.Member)
        author.id = 777
        channel = MagicMock(spec=discord.TextChannel)
        channel.name = "익명채널"

        await cog._send_anonymously(channel, author, "<:kipfel:123>", [])

        kwargs = webhook.send.await_args.kwargs
        self.assertEqual(
            kwargs["content"], "https://cdn.discordapp.com/emojis/123.png?size=4096"
        )
        self.assertTrue(kwargs["username"].startswith("익명("))
        # 실명이 어디에도 들어가지 않아야 한다.
        self.assertNotIn("777", kwargs["username"])

    async def test_plain_text_is_sent_verbatim(self):
        cog = make_anon_cog()
        webhook = AsyncMock()
        cog._get_webhook = AsyncMock(return_value=webhook)
        author = MagicMock(spec=discord.Member)
        author.id = 777

        await cog._send_anonymously(MagicMock(), author, "안녕하세요", [])
        self.assertEqual(webhook.send.await_args.kwargs["content"], "안녕하세요")

    async def test_mixed_text_and_emoji_is_sent_verbatim(self):
        cog = make_anon_cog()
        webhook = AsyncMock()
        cog._get_webhook = AsyncMock(return_value=webhook)
        author = MagicMock(spec=discord.Member)
        author.id = 777

        await cog._send_anonymously(MagicMock(), author, "안녕 <:kipfel:123>", [])
        self.assertEqual(webhook.send.await_args.kwargs["content"], "안녕 <:kipfel:123>")

    async def test_webhook_unavailable_returns_false(self):
        cog = make_anon_cog()
        cog._get_webhook = AsyncMock(return_value=None)
        author = MagicMock(spec=discord.Member)
        author.id = 777
        self.assertFalse(await cog._send_anonymously(MagicMock(), author, "hi", []))


class EmojiExpanderGuardTest(unittest.IsolatedAsyncioTestCase):
    """FR-5 실제 원인: EmojiExpander 가 익명 채널을 처리하면 실명이 노출된다."""

    def setUp(self):
        from cogs.emoje import EmojiExpander

        import re as _re

        self.cog = EmojiExpander.__new__(EmojiExpander)
        self.cog.bot = MagicMock()
        self.cog.emoji_pattern = _re.compile(r"^<(a?):(.+?):([0-9]+)>$")
        self.cog.get_webhook = AsyncMock()
        self._original_anon_ids = config.ANONYMOUS_CHANNEL_IDS
        config.ANONYMOUS_CHANNEL_IDS = frozenset({ANON_CHANNEL_ID})

    def tearDown(self):
        config.ANONYMOUS_CHANNEL_IDS = self._original_anon_ids

    async def test_anonymous_channel_is_skipped(self):
        message = make_message("<:kipfel:123>", channel_id=ANON_CHANNEL_ID)
        await self.cog.on_message(message)
        message.delete.assert_not_awaited()
        self.cog.get_webhook.assert_not_awaited()

    async def test_other_channels_still_expand(self):
        message = make_message("<:kipfel:123>", channel_id=OTHER_CHANNEL_ID)
        webhook = AsyncMock()
        self.cog.get_webhook = AsyncMock(return_value=webhook)

        await self.cog.on_message(message)
        message.delete.assert_awaited_once()
        self.assertIn("4096", webhook.send.await_args.kwargs["content"])

    async def test_non_emoji_message_is_untouched(self):
        message = make_message("일반 텍스트", channel_id=OTHER_CHANNEL_ID)
        await self.cog.on_message(message)
        message.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
