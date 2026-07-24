import discord
from discord.ext import commands
import json
import os
import re
import io
import aiohttp
from typing import Set, Optional
import logging

logger = logging.getLogger(__name__)

PIN_EMOJI = "⭐"
MIN_REACTIONS = 5
SUPER_REACTIONS = 30
FORWARDED_DB_FILE = "forwarded.json"
EMBED_COLOR = 0x5865F2

class ReactionForward(commands.Cog):
    """
    📌 반응 시 메시지를 지정된 채널로 포워딩하는 Cog
    """

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        # 환경 변수에서 TARGET_CHANNEL_ID를 가져옵니다. 값이 없으면 None이 됩니다.
        channel_id_str = os.getenv("TARGET_CHANNEL_ID")
        self.target_channel_id = int(channel_id_str.strip()) if channel_id_str and channel_id_str.strip() else None
        
        if not self.target_channel_id:
            print("⚠️ TARGET_CHANNEL_ID 환경 변수가 설정되지 않았습니다. Reaction Forward 기능이 제대로 작동하지 않을 수 있습니다.")
            logger.warning("TARGET_CHANNEL_ID is not set.")
            
        self._parse_source_channels()
        
        self.db_file = FORWARDED_DB_FILE
        self.forwarded_messages: Set[int] = self._load_forwarded_ids()
        self.super_announced: Set[int] = set()  # 특대왕 킵쭬 이미 알림한 메시지 ID

    def _parse_source_channels(self):
        """환경 변수에서 감지할 특정 채널 ID 목록을 불러옵니다."""
        source_channels_str = os.getenv("SOURCE_CHANNEL_IDS", "")
        if source_channels_str:
            try:
                self.source_channel_ids = {
                    int(id_str.strip())
                    for id_str in source_channels_str.split(",")
                    if id_str.strip()
                }
                logger.info(f"Loaded {len(self.source_channel_ids)} source channels for reaction forward.")
            except ValueError as e:
                print(f"⚠️ SOURCE_CHANNEL_IDS 파싱 오류: {e}")
                logger.warning(f"SOURCE_CHANNEL_IDS parsing error: {e}")
                self.source_channel_ids = set()
        else:
            self.source_channel_ids = set()

    def _load_forwarded_ids(self) -> Set[int]:
        """JSON 파일에서 이미 포워딩된 메시지 ID 목록을 로드"""
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return set(data.get("forwarded_message_ids", []))
            except Exception as e:
                print(f"❌ 데이터 로드 에러 (forwarded.json): {e}")
                logger.error(f"Error loading forwarded IDs: {e}")
                return set()
        return set()

    def _save_forwarded_ids(self):
        """현재 포워딩된 메시지 ID 목록을 JSON 파일에 저장"""
        try:
            data = {"forwarded_message_ids": list(self.forwarded_messages)}
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"❌ 데이터 저장 에러 (forwarded.json): {e}")
            logger.error(f"Error saving forwarded IDs: {e}")

    def _is_forwarded(self, message_id: int) -> bool:
        """메시지가 이미 포워딩되었는지 확인"""
        return message_id in self.forwarded_messages

    def _mark_forwarded(self, message_id: int):
        """메시지를 포워딩됨으로 표시하고 저장"""
        self.forwarded_messages.add(message_id)
        self._save_forwarded_ids()

    async def _get_target_channel(self) -> Optional[discord.TextChannel]:
        """대상 채널 객체를 가져옴"""
        if not self.target_channel_id:
            return None
        channel = self.bot.get_channel(self.target_channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(self.target_channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.error(f"Error fetching target channel {self.target_channel_id}: {e}")
                return None
        return channel

    async def _fetch_original_message(self, payload: discord.RawReactionActionEvent) -> Optional[discord.Message]:
        """원본 메시지를 API로부터 가져옴"""
        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.error(f"Error fetching source channel {payload.channel_id}: {e}")
                return None
            
        try:
            message = await channel.fetch_message(payload.message_id)
            return message
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Error fetching source message {payload.message_id}: {e}")
            return None

    @staticmethod
    def _extract_custom_emojis(content: str) -> list[tuple[str, str, bool]]:
        """
        메시지 내용에서 커스텀 이모지 정보를 추출합니다.
        반환값: [(name, id, is_animated), ...]
        """
        # 애니메이션: <a:name:id>
        animated = [(name, eid, True) for name, eid in re.findall(r"<a:(\w+):(\d+)>", content)]
        # 일반: <:name:id>
        static = [(name, eid, False) for name, eid in re.findall(r"<:(\w+):(\d+)>", content)]
        return animated + static

    @staticmethod
    def _emoji_cdn_url(emoji_id: str, animated: bool) -> str:
        """이모지 ID로 Discord CDN URL을 생성합니다."""
        ext = "gif" if animated else "png"
        return f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size=256"

    @staticmethod
    def _sanitize_content(content: str) -> str:
        """
        embed description에 커스텀 이모지가 CDN URL로 표시되는 문제를 방지합니다.
        <:name:id> 또는 <a:name:id> 형태를 :name: 텍스트로 변환합니다.
        """
        content = re.sub(r"<a:(\w+):\d+>", r":\1:", content)
        content = re.sub(r"<:(\w+):\d+>", r":\1:", content)
        return content

    def _create_forward_embed(self, message: discord.Message) -> discord.Embed:
        """포워딩할 임베드를 생성"""
        raw_content = message.content or ""

        # 커스텀 이모지 추출
        custom_emojis = self._extract_custom_emojis(raw_content)

        # description: 이모지 태그를 :name: 텍스트로 변환
        description = self._sanitize_content(raw_content) if raw_content else "*첨부 파일만 포함*"

        embed = discord.Embed(
            description=description,
            color=EMBED_COLOR,
            timestamp=message.created_at
        )

        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url if message.author.display_avatar else None
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
            # 커스텀 이모지가 있을 때: 이모지 이미지를 embed에 표시
            name, eid, animated = custom_emojis[0]
            emoji_url = self._emoji_cdn_url(eid, animated)

            # 메시지가 이모지만 있으면 크게(image), 텍스트도 있으면 작게(thumbnail)
            only_emojis = re.sub(r"<a?:\w+:\d+>", "", raw_content).strip() == ""
            if only_emojis:
                embed.set_image(url=emoji_url)
            else:
                embed.set_thumbnail(url=emoji_url)

        # 원본 메시지 링크
        embed.add_field(
            name="📌 원본 메시지",
            value=f"[여기를 클릭하세요]({message.jump_url})",
            inline=False
        )

        return embed

    # 영상/오디오로 판별할 MIME 타입 접두사
    VIDEO_MIME_PREFIXES = ("video/", "audio/")
    # 파일 크기 제한 (Discord 기본 업로드 제한: 25MB)
    MAX_FILE_SIZE = 25 * 1024 * 1024

    async def _send_video_attachments(self, target_channel: discord.TextChannel, message: discord.Message):
        """원본 메시지의 영상/오디오 첨부파일을 다운로드하여 대상 채널에 전송"""
        video_attachments = [
            att for att in message.attachments
            if att.content_type and att.content_type.startswith(self.VIDEO_MIME_PREFIXES)
        ]

        if not video_attachments:
            return

        async with aiohttp.ClientSession() as session:
            for attachment in video_attachments:
                if attachment.size > self.MAX_FILE_SIZE:
                    # 파일이 너무 크면 링크만 전송
                    await target_channel.send(
                        f"🎬 영상 (용량 초과로 링크 전송): {attachment.url}"
                    )
                    logger.info(f"Video attachment too large ({attachment.size} bytes), sent as link.")
                    continue

                try:
                    async with session.get(attachment.url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            file_obj = discord.File(
                                fp=io.BytesIO(data),
                                filename=attachment.filename
                            )
                            await target_channel.send(file=file_obj)
                            logger.info(f"Video attachment '{attachment.filename}' sent successfully.")
                        else:
                            await target_channel.send(
                                f"🎬 영상 다운로드 실패 (status {resp.status}): {attachment.url}"
                            )
                except Exception as e:
                    logger.error(f"Error downloading/sending video attachment: {e}")
                    await target_channel.send(
                        f"🎬 영상 전송 중 오류 발생: {attachment.url}"
                    )

    async def _forward_message_safe(self, payload: discord.RawReactionActionEvent, original_message: Optional[discord.Message] = None):
        """안전한 메시지 포워딩 (개별 오류 처리)"""
        try:
            target_channel = await self._get_target_channel()
            if not target_channel:
                print(f"❌ 대상 채널 {self.target_channel_id}를 찾을 수 없음")
                return

            if original_message is None:
                original_message = await self._fetch_original_message(payload)
                
            if not original_message:
                print(f"❌ 메시지 {payload.message_id}를 가져올 수 없음")
                return

            embed = self._create_forward_embed(original_message)

            await target_channel.send(embed=embed)

            # 영상/오디오 첨부파일은 별도로 전송하여 인라인 재생 가능하게
            await self._send_video_attachments(target_channel, original_message)

            print(f"✅ 메시지 {payload.message_id} 포워딩 완료")
            logger.info(f"Message {payload.message_id} forwarded successfully.")

            self._mark_forwarded(payload.message_id)

        except discord.Forbidden:
            print(f"❌ 권한 없음: 채널 {self.target_channel_id}에 접근/쓰기 불가")
            logger.error(f"Forbidden: Cannot write to channel {self.target_channel_id}")
        except discord.NotFound:
            print(f"❌ 찾을 수 없음: 메시지 또는 채널이 삭제됨")
            logger.error("NotFound: Message or channel deleted")
        except discord.HTTPException as e:
            print(f"❌ Discord API 오류: {e}")
            logger.error(f"Discord API Error: {e}")
        except Exception as e:
            print(f"❌ 포워딩 중 예외: {e}")
            logger.error(f"Exception during forwarding: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """모든 반응 추가 이벤트를 감지 (캐시되지 않은 메시지 포함)"""
        try:
            if payload.user_id == self.bot.user.id:
                return

            if str(payload.emoji) != PIN_EMOJI:
                return

            # 특정 채널만 감지하도록 설정된 경우 필터링
            if self.source_channel_ids and payload.channel_id not in self.source_channel_ids:
                return

            if self._is_forwarded(payload.message_id):
                print(f"ℹ️ 메시지 {payload.message_id}는 이미 포워딩됨. 무시.")
                logger.info(f"Message {payload.message_id} already forwarded. Ignored.")
                return

            # 원본 메시지를 가져와서 반응 개수를 확인합니다.
            original_message = await self._fetch_original_message(payload)
            if not original_message:
                return

            reaction_count = 0
            for reaction in original_message.reactions:
                if str(reaction.emoji) == PIN_EMOJI:
                    reaction_count = reaction.count
                    break

            if reaction_count < MIN_REACTIONS:
                # 5개 미만이면 무시
                return

            # 30개 이상이면 특대왕 킾추 ㅋㅋ 알림 (1회만)
            if reaction_count >= SUPER_REACTIONS and payload.message_id not in self.super_announced:
                self.super_announced.add(payload.message_id)
                target_channel = await self._get_target_channel()
                if target_channel:
                    await target_channel.send(
                        f"⭐⭐⭐ **특대왕 킾추 ㅋㅋ** ⭐⭐⭐\n{original_message.jump_url}"
                    )

            await self._forward_message_safe(payload, original_message)

        except Exception as e:
            print(f"❌ on_raw_reaction_add 예외: {e}")
            logger.error(f"Exception in on_raw_reaction_add: {e}")
            import traceback
            traceback.print_exc()

def setup(bot: discord.Bot):
    bot.add_cog(ReactionForward(bot))
