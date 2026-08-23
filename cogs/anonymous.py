"""익명 채팅 Cog.

FR-5 이모지 실명 노출 버그
--------------------------
**원인은 이 파일이 아니라 `cogs/emoje.py` 의 중복 처리였다.**
`EmojiExpander.on_message` 가 모든 TextChannel 에서 동작하면서 익명 채널을
제외하지 않아, 커스텀 이모지 단독 메시지 하나에 두 Cog 가 동시에 반응했다.

1. `emoje.py` → 웹훅으로 **작성자 실명/프사** + 4096px 이모지 이미지 전송 (실명 + 크게)
2. `anonymous.py` → 같은 메시지를 익명 웹훅으로 재전송 (익명 + 상대적으로 작게)

두 Cog 가 각자 `message.delete()` 를 호출해 하나는 성공하고 하나는 `NotFound` 로
실패했으며, 기존 `except Exception: pass` 가 그 실패를 삼켜 원인 추적이 불가능했다.

수정 내용
- `emoje.py` 가 익명 채널을 건너뛰도록 가드 추가 (중복 핸들러 제거)
- 익명 채널에서는 이 Cog 가 직접 이모지를 4096px CDN URL 로 확대 전송 →
  실명 노출 없이 큰 이모지를 유지
- `delete()` 를 웹훅 재전송보다 **먼저** 실행하고, 실패 시 `Forbidden`/`NotFound` 를
  구분해 로깅. 삭제 권한이 없으면 원본이 남으므로 익명 전송을 중단한다
  (그대로 보내면 실명 원본 + 익명 복사본 중복이 재발한다).
- Cog 초기화 및 메시지 처리 시 '메시지 관리'/'웹훅 관리' 권한을 확인해 경고 로그
"""

from __future__ import annotations

import hashlib
import logging
import re

import discord
from discord.ext import commands

import config

logger = logging.getLogger(__name__)

WEBHOOK_NAME = "AnonWebhook"
ANON_AVATAR_URL = "https://cdn.discordapp.com/embed/avatars/0.png"
# 커스텀 이모지 하나만 있는 메시지 (<:name:id> 또는 <a:name:id>)
CUSTOM_EMOJI_ONLY = re.compile(r"^<(a?):(\w+):(\d+)>$")


def emoji_image_url(content: str) -> str | None:
    """커스텀 이모지 단독 메시지면 고화질 CDN URL 을 반환한다(아니면 None)."""
    match = CUSTOM_EMOJI_ONLY.match((content or "").strip())
    if not match:
        return None
    extension = "gif" if match.group(1) else "png"
    return f"https://cdn.discordapp.com/emojis/{match.group(3)}.{extension}?size=4096"


class AnonymousChat(commands.Cog):
    """익명 채널의 메시지를 익명 웹훅으로 갈아끼운다."""

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.anon_channel_ids = config.ANONYMOUS_CHANNEL_IDS
        self.user_salts: dict[int, int] = {}
        self._permission_warned = False

        if not self.anon_channel_ids:
            logger.warning("익명 채널 설정이 없어 익명 채팅이 비활성화됩니다.")
        else:
            logger.info("익명 채널 %d개: %s", len(self.anon_channel_ids),
                        ", ".join(str(cid) for cid in sorted(self.anon_channel_ids)))

    # ------------------------------------------------------------------ #
    # 익명 ID
    # ------------------------------------------------------------------ #
    def get_anon_id(self, user_id: int) -> str:
        """유저 ID와 세탁 횟수만으로 가짜 IP 를 생성한다."""
        salt = self.user_salts.get(user_id, 0)
        hash_val = hashlib.sha256(f"{user_id}_{salt}".encode()).hexdigest()
        part1 = int(hash_val[:2], 16) % 256
        part2 = int(hash_val[2:4], 16) % 256
        return f"{part1}.{part2}.***.***"

    def log_secret(self, user: discord.abc.User, content: str, anon_ip: str) -> None:
        """작성자 추적용 기록. 디스코드에는 노출되지 않고 서버 로그에만 남는다."""
        logger.info("[익명작성] %s(%s) → 익명(%s): %s", user, user.id, anon_ip, content)

    # ------------------------------------------------------------------ #
    # 권한 확인
    # ------------------------------------------------------------------ #
    def _check_permissions(self, channel: discord.TextChannel) -> discord.Permissions | None:
        """봇의 채널 권한을 확인하고 부족하면 경고 로그를 남긴다."""
        me = channel.guild.me if channel.guild else None
        if me is None:
            return None
        perms = channel.permissions_for(me)
        missing = [
            name
            for name, ok in (
                ("메시지 관리", perms.manage_messages),
                ("웹훅 관리", perms.manage_webhooks),
            )
            if not ok
        ]
        if missing and not self._permission_warned:
            logger.warning(
                "익명 채널 #%s 에서 봇에게 다음 권한이 없습니다: %s."
                " 원본 메시지 삭제/익명 전송이 실패할 수 있습니다.",
                channel.name,
                ", ".join(missing),
            )
            self._permission_warned = True
        return perms

    # ------------------------------------------------------------------ #
    # 웹훅
    # ------------------------------------------------------------------ #
    async def _get_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        try:
            webhooks = await channel.webhooks()
            # 이름만 보고 재사용하면 안 된다. 다른 애플리케이션이 만든 웹훅은 Discord 가
            # token 을 내려주지 않아 webhook.send() 가 InvalidArgument 로 실패한다
            # (개발봇/운영봇을 같은 채널에 함께 쓸 때 발생). 토큰이 있는 것만 재사용한다.
            webhook = discord.utils.find(
                lambda w: w.name == WEBHOOK_NAME and w.token is not None, webhooks
            )
            if webhook is None:
                webhook = await channel.create_webhook(name=WEBHOOK_NAME)
                logger.info("익명 웹훅을 새로 생성했습니다 (#%s)", channel.name)
            return webhook
        except discord.Forbidden:
            logger.error(
                "권한 없음: #%s 에서 웹훅을 조회/생성할 수 없습니다. '웹훅 관리' 권한이 필요합니다.",
                channel.name,
            )
        except discord.HTTPException as exc:
            logger.error("웹훅 준비 실패 (#%s): %s", channel.name, exc)
        return None

    async def _send_anonymously(
        self,
        channel: discord.TextChannel,
        author: discord.abc.User,
        content: str,
        files: list[discord.File],
    ) -> bool:
        """익명 웹훅으로 전송한다. 커스텀 이모지 단독이면 고화질로 확대 전송."""
        webhook = await self._get_webhook(channel)
        if webhook is None:
            return False

        anon_ip = self.get_anon_id(author.id)
        self.log_secret(author, content, anon_ip)

        # 커스텀 이모지 단독 메시지는 CDN URL 로 보내 크게 표시한다.
        # (실명 노출 없이 기존 emoje.py 확대 동작을 익명 채널에서 대체)
        payload = emoji_image_url(content) or content

        try:
            await webhook.send(
                content=payload if payload else None,
                username=f"익명({anon_ip})",
                avatar_url=ANON_AVATAR_URL,
                files=files,
            )
            return True
        except discord.DiscordException as exc:
            # InvalidArgument(토큰 없는 웹훅 등)는 HTTPException 이 아니므로 함께 잡는다.
            logger.error("익명 전송 실패 (#%s): %s", channel.name, exc)
            return False

    # ------------------------------------------------------------------ #
    # 기능 1: 일반 채팅 자동 익명
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id not in self.anon_channel_ids:
            return

        content = message.content or ""
        author = message.author
        channel = message.channel

        self._check_permissions(channel)

        # 첨부파일은 원본이 삭제되기 전에 읽어둔다.
        files: list[discord.File] = []
        for attachment in message.attachments:
            try:
                files.append(await attachment.to_file())
            except discord.HTTPException as exc:
                logger.warning("첨부파일 읽기 실패 (%s): %s", attachment.filename, exc)

        if not content and not files:
            return

        # 1) 원본 메시지를 먼저 삭제한다 (웹훅 재전송보다 반드시 앞).
        if not await self._delete_original(message):
            return

        # 2) 그 다음 익명으로 재전송한다.
        await self._send_anonymously(channel, author, content, files)

    async def _delete_original(self, message: discord.Message) -> bool:
        """원본 삭제. 성공 시 True, 익명 전송을 중단해야 하면 False."""
        try:
            await message.delete()
            return True
        except discord.Forbidden:
            # 원본이 실명으로 남은 상태에서 익명 복사본을 보내면 중복 노출이 재발한다.
            logger.error(
                "권한 없음: 익명 채널 #%s 의 메시지(%s)를 삭제할 수 없어 익명 전송을 중단합니다."
                " 봇에게 '메시지 관리' 권한을 부여하세요.",
                getattr(message.channel, "name", message.channel.id),
                message.id,
            )
            return False
        except discord.NotFound:
            # 다른 처리기나 사용자가 이미 삭제한 경우 — 원본은 사라졌으므로 계속 진행한다.
            logger.warning(
                "메시지(%s)가 이미 삭제되어 있었습니다. 익명 전송은 계속합니다.", message.id
            )
            return True
        except discord.HTTPException as exc:
            logger.error("메시지(%s) 삭제 실패: %s", message.id, exc)
            return False

    # ------------------------------------------------------------------ #
    # 기능 2: /익명 명령어
    # ------------------------------------------------------------------ #
    @discord.slash_command(name="익명", description="완벽한 익명으로 전송합니다.")
    async def send_anon(
        self,
        ctx: discord.ApplicationContext,
        내용: str = discord.Option(str, "내용", required=False, default=""),
        첨부파일: discord.Attachment = discord.Option(
            discord.Attachment, "파일", required=False, default=None
        ),
    ):
        await ctx.defer(ephemeral=True)
        if ctx.channel.id not in self.anon_channel_ids:
            return await ctx.followup.send(
                "❌ 이 명령어는 익명 채널에서만 쓸 수 있습니다.", ephemeral=True
            )
        if not 내용 and not 첨부파일:
            return await ctx.followup.send("❌ 내용이나 파일을 입력하세요.", ephemeral=True)

        files: list[discord.File] = []
        if 첨부파일:
            try:
                files.append(await 첨부파일.to_file())
            except discord.HTTPException as exc:
                logger.warning("첨부파일 읽기 실패 (%s): %s", 첨부파일.filename, exc)
                return await ctx.followup.send("❌ 첨부파일을 읽지 못했습니다.", ephemeral=True)

        sent = await self._send_anonymously(ctx.channel, ctx.author, 내용, files)
        if sent:
            await ctx.followup.send("✅ 전송 완료!", ephemeral=True)
        else:
            await ctx.followup.send(
                "❌ 전송에 실패했습니다. 봇 권한을 확인해주세요. (로그에 원인이 기록됩니다)",
                ephemeral=True,
            )

    # ------------------------------------------------------------------ #
    # 기능 3: /ip변경
    # ------------------------------------------------------------------ #
    @discord.slash_command(name="ip변경", description="신분을 세탁합니다.")
    async def change_ip(self, ctx: discord.ApplicationContext):
        self.user_salts[ctx.author.id] = self.user_salts.get(ctx.author.id, 0) + 1
        new_ip = self.get_anon_id(ctx.author.id)
        logger.info("신분 세탁: %s(%s) → %s", ctx.author, ctx.author.id, new_ip)
        await ctx.respond(f"🔄 **신분 세탁 완료!** 새 IP: `{new_ip}`", ephemeral=True)


def setup(bot: discord.Bot):
    bot.add_cog(AnonymousChat(bot))
