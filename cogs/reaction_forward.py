import discord
from discord.ext import commands
import json
import os
from typing import Set, Optional
import logging

logger = logging.getLogger(__name__)

PIN_EMOJI = "📌"
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
            
        self.db_file = FORWARDED_DB_FILE
        self.forwarded_messages: Set[int] = self._load_forwarded_ids()

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

    def _create_forward_embed(self, message: discord.Message) -> discord.Embed:
        """포워딩할 임베드를 생성"""
        embed = discord.Embed(
            description=message.content or "*첨부 파일만 포함*",
            color=EMBED_COLOR,
            timestamp=message.created_at
        )

        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url if message.author.display_avatar else None
        )

        # 첫 번째 이미지 첨부
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    embed.set_image(url=attachment.url)
                    break

        # 원본 메시지 링크
        embed.add_field(
            name="📌 원본 메시지",
            value=f"[여기를 클릭하세요]({message.jump_url})",
            inline=False
        )

        return embed

    async def _forward_message_safe(self, payload: discord.RawReactionActionEvent):
        """안전한 메시지 포워딩 (개별 오류 처리)"""
        try:
            target_channel = await self._get_target_channel()
            if not target_channel:
                print(f"❌ 대상 채널 {self.target_channel_id}를 찾을 수 없음")
                return

            original_message = await self._fetch_original_message(payload)
            if not original_message:
                print(f"❌ 메시지 {payload.message_id}를 가져올 수 없음")
                return

            embed = self._create_forward_embed(original_message)

            await target_channel.send(embed=embed)
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

            if self._is_forwarded(payload.message_id):
                print(f"ℹ️ 메시지 {payload.message_id}는 이미 포워딩됨. 무시.")
                logger.info(f"Message {payload.message_id} already forwarded. Ignored.")
                return

            await self._forward_message_safe(payload)

        except Exception as e:
            print(f"❌ on_raw_reaction_add 예외: {e}")
            logger.error(f"Exception in on_raw_reaction_add: {e}")
            import traceback
            traceback.print_exc()

def setup(bot: discord.Bot):
    bot.add_cog(ReactionForward(bot))
