"""커스텀 이모지 단독 메시지를 고화질(4096px)로 확대해 다시 보내는 Cog.

FR-5 관련: 이 Cog 가 **익명 채널까지 처리하던 것이 실명 노출 버그의 원인**이었다.
익명 채널에서는 `cogs/anonymous.py` 가 익명 이름으로 같은 확대 처리를 하므로
여기서는 익명 채널을 건너뛴다. (두 Cog 가 같은 메시지를 각각 삭제·재전송하면
실명 웹훅 메시지가 먼저 노출된 뒤 익명 메시지가 중복 전송된다.)
"""

from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands

import config

logger = logging.getLogger(__name__)

WEBHOOK_NAME = "EmojiExpander Webhook"


class EmojiExpander(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        # 메시지 시작(^)부터 끝($)까지 이모지 하나만 있는지 확인하는 정규식
        self.emoji_pattern = re.compile(r"^<(a?):(.+?):([0-9]+)>$")

    async def get_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        """채널에서 봇이 사용할 웹후크를 찾거나 없으면 생성한다."""
        webhooks = await channel.webhooks()
        # 다른 애플리케이션이 만든 웹후크는 Discord 가 token 을 내려주지 않아
        # webhook.send() 가 InvalidArgument 로 실패한다. 토큰이 있는 것만 재사용한다.
        webhook = discord.utils.find(
            lambda w: w.name == WEBHOOK_NAME and w.token is not None, webhooks
        )
        if webhook is None:
            webhook = await channel.create_webhook(name=WEBHOOK_NAME)
        return webhook

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # 웹후크는 일반 텍스트 채널에서만 작동한다 (DM 등 제외)
        if not isinstance(message.channel, discord.TextChannel):
            return

        # 익명 채널은 anonymous.py 가 익명 이름으로 확대 전송을 담당한다.
        # 여기서 처리하면 작성자 실명이 노출된다 (FR-5).
        if message.channel.id in config.ANONYMOUS_CHANNEL_IDS:
            return

        match = self.emoji_pattern.match((message.content or "").strip())
        if not match:
            return

        is_animated = bool(match.group(1))
        emoji_id = match.group(3)
        extension = "gif" if is_animated else "png"
        emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?size=4096"

        # 1. 원본 메시지 삭제 (재전송보다 먼저)
        try:
            await message.delete()
        except discord.Forbidden:
            logger.warning(
                "권한 없음: #%s 에서 메시지를 삭제할 수 없어 확대를 건너뜁니다.",
                message.channel.name,
            )
            return
        except discord.NotFound:
            logger.info("메시지(%s)가 이미 삭제되어 확대를 건너뜁니다.", message.id)
            return
        except discord.HTTPException as exc:
            logger.error("메시지(%s) 삭제 실패: %s", message.id, exc)
            return

        # 2. 웹후크로 작성자를 흉내내어 전송
        try:
            webhook = await self.get_webhook(message.channel)
            await webhook.send(
                content=emoji_url,  # 임베드 없이 URL만 보내면 이미지가 크게 나온다
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url,
            )
        except discord.Forbidden:
            logger.error(
                "권한 없음: #%s 에서 웹후크를 관리할 수 없습니다.", message.channel.name
            )
        except discord.DiscordException as exc:
            # 원본 메시지를 이미 지운 뒤라 여기서 예외가 새어나가면 메시지만 사라진다.
            # InvalidArgument 는 HTTPException 이 아니므로 상위 타입으로 잡는다.
            logger.error("웹후크 전송 실패 (#%s): %s", message.channel.name, exc)


def setup(bot: discord.Bot):
    bot.add_cog(EmojiExpander(bot))
