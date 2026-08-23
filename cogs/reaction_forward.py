"""반응 시 메시지를 지정 채널로 포워딩하는 Cog.

FR-6 수정 내용
--------------
1. **포럼 포스트/댓글 미감지 버그 수정**: 포럼 포스트(스레드) 안의 메시지는
   `payload.channel_id` 가 **포럼 채널 ID가 아니라 스레드 ID** 다. 기존 코드는
   `SOURCE_CHANNEL_IDS` 에 포럼 채널 ID를 넣어도 전부 필터링해버렸다.
   이제 스레드의 `parent_id` 를 확인해 부모 포럼/텍스트 채널로도 매칭한다.
2. `on_raw_reaction_remove` 를 추가해 반응 취소 시 상태를 동기화한다
   (이미 포워딩된 메시지는 되돌리지 않는다 — 중복 포워딩 방지).
3. 캐시 밖 메시지도 `channel.fetch_message()` 로 직접 조회한다(기존 유지).
   불필요한 fetch 를 막기 위해 이모지/채널 필터를 먼저 통과시킨다.
4. `print` 제거 후 `logging` 으로 통일, 설정값은 `config` 로 분리,
   중복 기록 파일 저장은 `asyncio.to_thread` 로 이벤트 루프 블로킹을 피한다.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
from typing import Optional, Set

import aiohttp
import discord
from discord.ext import commands

import config

logger = logging.getLogger(__name__)

PIN_EMOJI = config.REACTION_PIN_EMOJI
MIN_REACTIONS = config.REACTION_MIN_COUNT
SUPER_REACTIONS = config.REACTION_SUPER_COUNT
EMBED_COLOR = 0x5865F2


class ReactionForward(commands.Cog):
    """📌 반응 시 메시지를 지정된 채널로 포워딩하는 Cog"""

    # 영상/오디오로 판별할 MIME 타입 접두사
    VIDEO_MIME_PREFIXES = ("video/", "audio/")
    # 파일 크기 제한 (Discord 기본 업로드 제한: 25MB)
    MAX_FILE_SIZE = 25 * 1024 * 1024

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.target_channel_id = config.TARGET_CHANNEL_ID
        self.source_channel_ids: frozenset[int] = config.SOURCE_CHANNEL_IDS

        if not self.target_channel_id:
            logger.warning(
                "TARGET_CHANNEL_ID 가 설정되지 않아 반응 포워딩이 동작하지 않습니다."
            )
        if self.source_channel_ids:
            logger.info(
                "반응 감지 대상 채널 %d개 (스레드는 부모 채널로도 매칭됩니다).",
                len(self.source_channel_ids),
            )
        else:
            logger.info("SOURCE_CHANNEL_IDS 가 비어 있어 모든 채널의 반응을 감지합니다.")

        self.db_file = config.FORWARDED_DB_PATH
        self.forwarded_messages: Set[int] = self._load_forwarded_ids()
        self.super_announced: Set[int] = set()  # 특대왕 킾추 알림을 이미 보낸 메시지 ID
        # 스레드 ID → 부모 채널 ID 캐시 (rate limit 절약)
        self._parent_cache: dict[int, Optional[int]] = {}

    # ------------------------------------------------------------------ #
    # 중복 포워딩 기록
    # ------------------------------------------------------------------ #
    def _load_forwarded_ids(self) -> Set[int]:
        if not os.path.exists(self.db_file):
            return set()
        try:
            with open(self.db_file, "r", encoding="utf-8") as file:
                data = json.load(file)
                return set(data.get("forwarded_message_ids", []))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.error("포워딩 기록 로드 실패 (%s): %s", self.db_file, exc)
            return set()

    def _write_forwarded_ids(self, message_ids: list[int]) -> None:
        with open(self.db_file, "w", encoding="utf-8") as file:
            json.dump({"forwarded_message_ids": message_ids}, file, ensure_ascii=False, indent=4)

    async def _save_forwarded_ids(self) -> None:
        try:
            # 파일 쓰기는 스레드로 넘겨 이벤트 루프를 블로킹하지 않는다.
            await asyncio.to_thread(self._write_forwarded_ids, list(self.forwarded_messages))
        except OSError as exc:
            logger.error("포워딩 기록 저장 실패 (%s): %s", self.db_file, exc)

    def _is_forwarded(self, message_id: int) -> bool:
        return message_id in self.forwarded_messages

    async def _mark_forwarded(self, message_id: int) -> None:
        self.forwarded_messages.add(message_id)
        await self._save_forwarded_ids()

    # ------------------------------------------------------------------ #
    # 채널 / 메시지 조회
    # ------------------------------------------------------------------ #
    async def _get_channel(self, channel_id: int):
        """캐시 → API 순으로 채널(또는 스레드)을 가져온다."""
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            logger.error("채널 조회 실패 (%s): %s", channel_id, exc)
            return None

    async def _get_target_channel(self) -> Optional[discord.abc.Messageable]:
        if not self.target_channel_id:
            return None
        return await self._get_channel(self.target_channel_id)

    async def _resolve_parent_id(self, channel_id: int) -> Optional[int]:
        """스레드라면 부모 채널 ID를 돌려준다. 스레드가 아니면 None.

        포럼 포스트/댓글의 반응은 `payload.channel_id` 가 스레드 ID로 오기 때문에
        부모 포럼 채널로 매칭하려면 이 변환이 필요하다.
        """
        if channel_id in self._parent_cache:
            return self._parent_cache[channel_id]

        channel = await self._get_channel(channel_id)
        parent_id = channel.parent_id if isinstance(channel, discord.Thread) else None
        self._parent_cache[channel_id] = parent_id
        return parent_id

    async def _is_watched_channel(self, channel_id: int) -> bool:
        """감지 대상 채널인지 판정한다(스레드는 부모 채널로도 매칭)."""
        if not self.source_channel_ids:
            return True
        if channel_id in self.source_channel_ids:
            return True
        parent_id = await self._resolve_parent_id(channel_id)
        if parent_id is not None and parent_id in self.source_channel_ids:
            logger.debug("스레드 %s → 부모 채널 %s 로 매칭됨", channel_id, parent_id)
            return True
        return False

    async def _fetch_original_message(
        self, payload: discord.RawReactionActionEvent
    ) -> Optional[discord.Message]:
        """원본 메시지를 API로부터 가져온다(캐시에 없는 오래된 메시지도 조회 가능)."""
        channel = await self._get_channel(payload.channel_id)
        if channel is None:
            return None
        try:
            return await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            logger.error("원본 메시지 조회 실패 (%s): %s", payload.message_id, exc)
            return None

    @staticmethod
    def _would_deanonymize(message: discord.Message) -> bool:
        """익명 채널의 메시지인데 익명 웹훅이 보낸 게 아니면 True.

        익명 채널에서는 `anonymous.py` 가 원본을 지우고 익명 웹훅으로 다시 올린다.
        원본이 그대로 남아 있다는 건 그 삭제가 실패했다는 뜻이고, 이걸 포워딩하면
        작성자 실명이 대상 채널에 그대로 드러난다. 그런 메시지는 보내지 않는다.
        """
        if message.channel.id not in config.ANONYMOUS_CHANNEL_IDS:
            return False
        return message.webhook_id is None

    @staticmethod
    def _count_reactions(message: discord.Message) -> int:
        for reaction in message.reactions:
            if str(reaction.emoji) == PIN_EMOJI:
                return reaction.count
        return 0

    # ------------------------------------------------------------------ #
    # 임베드 생성
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_custom_emojis(content: str) -> list[tuple[str, str, bool]]:
        """메시지 내용에서 커스텀 이모지 정보를 추출한다. [(name, id, is_animated), ...]"""
        animated = [(name, eid, True) for name, eid in re.findall(r"<a:(\w+):(\d+)>", content)]
        static = [(name, eid, False) for name, eid in re.findall(r"<:(\w+):(\d+)>", content)]
        return animated + static

    @staticmethod
    def _emoji_cdn_url(emoji_id: str, animated: bool) -> str:
        ext = "gif" if animated else "png"
        return f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size=256"

    @staticmethod
    def _sanitize_content(content: str) -> str:
        """embed description 에 커스텀 이모지가 CDN URL로 표시되는 문제를 방지한다."""
        content = re.sub(r"<a:(\w+):\d+>", r":\1:", content)
        content = re.sub(r"<:(\w+):\d+>", r":\1:", content)
        return content

    def _create_forward_embed(self, message: discord.Message) -> discord.Embed:
        raw_content = message.content or ""
        custom_emojis = self._extract_custom_emojis(raw_content)
        description = self._sanitize_content(raw_content) if raw_content else "*첨부 파일만 포함*"

        embed = discord.Embed(
            description=description, color=EMBED_COLOR, timestamp=message.created_at
        )
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
        )

        # 이미지 우선순위: 첨부파일 > 커스텀 이모지 (영상은 별도 전송)
        image_set = False
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    embed.set_image(url=attachment.url)
                    image_set = True
                    break

        if not image_set and custom_emojis:
            name, eid, animated = custom_emojis[0]
            emoji_url = self._emoji_cdn_url(eid, animated)
            only_emojis = re.sub(r"<a?:\w+:\d+>", "", raw_content).strip() == ""
            if only_emojis:
                embed.set_image(url=emoji_url)
            else:
                embed.set_thumbnail(url=emoji_url)

        embed.add_field(
            name="📌 원본 메시지",
            value=f"[여기를 클릭하세요]({message.jump_url})",
            inline=False,
        )
        return embed

    async def _send_video_attachments(self, target_channel, message: discord.Message):
        """원본 메시지의 영상/오디오 첨부파일을 대상 채널에 전송한다."""
        video_attachments = [
            att
            for att in message.attachments
            if att.content_type and att.content_type.startswith(self.VIDEO_MIME_PREFIXES)
        ]
        if not video_attachments:
            return

        async with aiohttp.ClientSession() as session:
            for attachment in video_attachments:
                if attachment.size > self.MAX_FILE_SIZE:
                    await target_channel.send(f"🎬 영상 (용량 초과로 링크 전송): {attachment.url}")
                    logger.info("영상 첨부가 너무 커서 링크로 전송 (%d bytes)", attachment.size)
                    continue
                try:
                    async with session.get(attachment.url) as response:
                        if response.status == 200:
                            data = await response.read()
                            await target_channel.send(
                                file=discord.File(fp=io.BytesIO(data), filename=attachment.filename)
                            )
                            logger.info("영상 첨부 '%s' 전송 완료", attachment.filename)
                        else:
                            await target_channel.send(
                                f"🎬 영상 다운로드 실패 (status {response.status}): {attachment.url}"
                            )
                except (aiohttp.ClientError, asyncio.TimeoutError, discord.HTTPException) as exc:
                    logger.error("영상 첨부 전송 중 오류: %s", exc)
                    await target_channel.send(f"🎬 영상 전송 중 오류 발생: {attachment.url}")

    async def _forward_message_safe(
        self,
        payload: discord.RawReactionActionEvent,
        original_message: Optional[discord.Message] = None,
    ):
        """안전한 메시지 포워딩 (개별 오류 처리)"""
        try:
            target_channel = await self._get_target_channel()
            if target_channel is None:
                logger.error("대상 채널 %s 를 찾을 수 없습니다.", self.target_channel_id)
                return

            if original_message is None:
                original_message = await self._fetch_original_message(payload)
            if original_message is None:
                logger.error("메시지 %s 를 가져올 수 없습니다.", payload.message_id)
                return

            if self._would_deanonymize(original_message):
                logger.warning(
                    "익명 채널의 실명 메시지 %s 는 포워딩하지 않습니다 "
                    "(익명 웹훅이 아님 — 원본 삭제가 실패한 것으로 보입니다).",
                    payload.message_id,
                )
                return

            await target_channel.send(embed=self._create_forward_embed(original_message))
            # 영상/오디오 첨부파일은 별도로 전송하여 인라인 재생 가능하게
            await self._send_video_attachments(target_channel, original_message)

            logger.info("메시지 %s 포워딩 완료", payload.message_id)
            await self._mark_forwarded(payload.message_id)

        except discord.Forbidden:
            logger.error("권한 없음: 채널 %s 에 접근/쓰기 불가", self.target_channel_id)
        except discord.NotFound:
            logger.error("찾을 수 없음: 메시지 또는 채널이 삭제됨 (%s)", payload.message_id)
        except discord.HTTPException as exc:
            logger.error("Discord API 오류: %s", exc)
        except Exception:
            logger.exception("포워딩 중 예외 (message_id=%s)", payload.message_id)

    # ------------------------------------------------------------------ #
    # 이벤트 — raw 이벤트만 사용해 캐시 밖 메시지도 감지한다
    # ------------------------------------------------------------------ #
    def _is_own_reaction(self, payload: discord.RawReactionActionEvent) -> bool:
        return self.bot.user is not None and payload.user_id == self.bot.user.id

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """모든 반응 추가 이벤트를 감지 (캐시되지 않은 메시지 · 포럼 스레드 포함)"""
        try:
            if self._is_own_reaction(payload):
                return
            if str(payload.emoji) != PIN_EMOJI:
                return
            if not await self._is_watched_channel(payload.channel_id):
                return
            if self._is_forwarded(payload.message_id):
                logger.info("메시지 %s 는 이미 포워딩됨. 무시.", payload.message_id)
                return

            # 여기서부터 반응 개수 확인을 위해 원본 메시지를 조회한다.
            original_message = await self._fetch_original_message(payload)
            if original_message is None:
                return

            reaction_count = self._count_reactions(original_message)
            if reaction_count < MIN_REACTIONS:
                return

            if (
                reaction_count >= SUPER_REACTIONS
                and payload.message_id not in self.super_announced
            ):
                self.super_announced.add(payload.message_id)
                target_channel = await self._get_target_channel()
                if target_channel is not None:
                    await target_channel.send(
                        f"⭐⭐⭐ **특대왕 킾추 ㅋㅋ** ⭐⭐⭐\n{original_message.jump_url}"
                    )

            await self._forward_message_safe(payload, original_message)

        except Exception:
            logger.exception("on_raw_reaction_add 처리 중 예외")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """반응 취소 시 상태만 동기화한다.

        이미 포워딩한 메시지는 되돌리지 않는다(대상 채널에 남은 메시지를 지우지 않으므로
        같은 메시지가 여러 번 쌓이는 것을 막기 위함). 특대왕 알림 플래그만
        기준 미달로 내려가면 해제해 재도달 시 다시 알릴 수 있게 한다.
        """
        try:
            if self._is_own_reaction(payload):
                return
            if str(payload.emoji) != PIN_EMOJI:
                return
            if not await self._is_watched_channel(payload.channel_id):
                return

            logger.info(
                "반응 취소 감지: message_id=%s channel_id=%s user_id=%s",
                payload.message_id,
                payload.channel_id,
                payload.user_id,
            )

            # 상태 동기화가 필요한 경우에만 fetch 한다(rate limit 절약).
            if payload.message_id not in self.super_announced:
                return

            original_message = await self._fetch_original_message(payload)
            if original_message is None:
                return

            if self._count_reactions(original_message) < SUPER_REACTIONS:
                self.super_announced.discard(payload.message_id)
                logger.info(
                    "특대왕 알림 플래그 해제 (message_id=%s, %d개 미만)",
                    payload.message_id,
                    SUPER_REACTIONS,
                )
        except Exception:
            logger.exception("on_raw_reaction_remove 처리 중 예외")


def setup(bot: discord.Bot):
    bot.add_cog(ReactionForward(bot))
