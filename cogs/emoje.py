import discord
from discord.ext import commands
import re

class EmojiExpander(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 메시지 시작(^)부터 끝($)까지 이모지 하나만 있는지 확인하는 정규식
        self.emoji_pattern = re.compile(r"^<(a?):(.+?):([0-9]+)>$")

    async def get_webhook(self, channel):
        """채널에서 봇이 사용할 웹후크를 찾거나 없으면 생성합니다."""
        webhooks = await channel.webhooks()
        
        # 봇이 만든 웹후크가 이미 있는지 확인 ('EmojiExpander'라는 이름으로 찾음)
        webhook = discord.utils.get(webhooks, name="EmojiExpander Webhook")
        
        if webhook is None:
            # 없으면 새로 생성
            webhook = await channel.create_webhook(name="EmojiExpander Webhook")
            
        return webhook

    @commands.Cog.listener()
    async def on_message(self, message):
        # 봇이 보낸 메시지는 무시
        if message.author.bot:
            return

        # 웹후크는 일반 텍스트 채널에서만 작동합니다 (DM 등 제외)
        if not isinstance(message.channel, discord.TextChannel):
            return

        content = message.content.strip()
        match = self.emoji_pattern.match(content)

        if match:
            # 이모지 정보 추출
            is_animated = bool(match.group(1))
            emoji_id = match.group(3)
            extension = "gif" if is_animated else "png"
            
            # 고화질 이미지 URL 생성
            emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?size=4096"

            # 1. 원본 메시지 삭제 시도
            try:
                await message.delete()
            except discord.Forbidden:
                # 삭제 권한이 없으면 기능을 수행하지 않거나 로그만 남김
                print(f"권한 부족: {message.channel.name}에서 메시지를 삭제할 수 없습니다.")
                return
            except Exception as e:
                print(f"메시지 삭제 중 오류: {e}")
                return

            # 2. 웹후크를 통해 사용자 흉내내어 전송
            try:
                webhook = await self.get_webhook(message.channel)
                
                await webhook.send(
                    content=emoji_url,  # 임베드 없이 URL만 보내면 이미지가 크게 나옵니다
                    username=message.author.display_name, # 사용자의 닉네임 사용
                    avatar_url=message.author.display_avatar.url # 사용자의 프사 사용
                )
            except discord.Forbidden:
                print(f"권한 부족: {message.channel.name}에서 웹후크를 관리할 수 없습니다.")
            except Exception as e:
                print(f"웹후크 전송 중 오류: {e}")

def setup(bot):
    bot.add_cog(EmojiExpander(bot))