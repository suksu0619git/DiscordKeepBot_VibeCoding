import discord
from discord.ext import commands
import hashlib
from datetime import datetime

class AnonymousChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.anon_channel_id = 1480391682817130719
        self.user_salts = {}

    def get_anon_id(self, user_id):
        # 유저 ID와 세탁 횟수만으로 가짜 IP 생성
        salt = self.user_salts.get(user_id, 0)
        hash_target = f"{user_id}_{salt}"
        hash_val = hashlib.sha256(hash_target.encode()).hexdigest()
        
        part1 = int(hash_val[:2], 16) % 256
        part2 = int(hash_val[2:4], 16) % 256
        return f"{part1}.{part2}.***.***"

    # ==========================================
    # 관리자 전용 비밀 로그 기록 함수
    # ==========================================
    def print_secret_log(self, user, content, ip):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 디스코드에는 안 뜨고 터미널(nohup.out)에만 기록됩니다.
        print(f"🚨 [{now}] {user.name}({user.id}) 님이 익명({ip})으로 작성: {content}")

    # ==========================================
    # 기능 1: 일반 채팅 자동 익명
    # ==========================================
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.channel.id != self.anon_channel_id:
            return

        content = message.content
        author = message.author
        files = []
        for attachment in message.attachments:
            files.append(await attachment.to_file())

        if not content and not files: return

        # 메시지 즉시 삭제
        try:
            await message.delete()
        except Exception:
            pass

        try:
            webhooks = await message.channel.webhooks()
            webhook = next((wh for wh in webhooks if wh.name == "AnonWebhook"), None)
            if not webhook:
                webhook = await message.channel.create_webhook(name="AnonWebhook")

            anon_ip = self.get_anon_id(author.id)
            
            # 🔥 터미널에 범인(작성자) 기록 남기기
            self.print_secret_log(author, content, anon_ip)

            await webhook.send(
                content=content if content else None,
                username=f"익명({anon_ip})",
                avatar_url="https://cdn.discordapp.com/embed/avatars/0.png",
                files=files
            )
        except Exception as e:
            print(f"에러: {e}")

    # ==========================================
    # 기능 2: /익명 명령어
    # ==========================================
    @discord.slash_command(name="익명", description="완벽한 익명으로 전송합니다.")
    async def send_anon(
        self, ctx: discord.ApplicationContext,
        내용: str = discord.Option(str, "내용", required=False, default=""),
        첨부파일: discord.Attachment = discord.Option(discord.Attachment, "파일", required=False, default=None)
    ):
        await ctx.defer(ephemeral=True)
        if ctx.channel.id != self.anon_channel_id:
            return await ctx.followup.send("❌ 이 명령어는 익명 채널에서만 쓸 수 있습니다.", ephemeral=True)
        if not 내용 and not 첨부파일:
            return await ctx.followup.send("❌ 내용이나 파일을 입력하세요.", ephemeral=True)

        try:
            webhooks = await ctx.channel.webhooks()
            webhook = next((wh for wh in webhooks if wh.name == "AnonWebhook"), None)
            if not webhook: 
                webhook = await ctx.channel.create_webhook(name="AnonWebhook")

            files = []
            if 첨부파일: 
                files.append(await 첨부파일.to_file())

            anon_ip = self.get_anon_id(ctx.author.id)
            
            # 🔥 터미널에 범인(작성자) 기록 남기기
            self.print_secret_log(ctx.author, 내용, anon_ip)

            await webhook.send(
                content=내용 if 내용 else None,
                username=f"익명({anon_ip})",
                avatar_url="https://cdn.discordapp.com/embed/avatars/0.png",
                files=files
            )
            await ctx.followup.send("✅ 전송 완료!", ephemeral=True)
        except Exception as e:
            await ctx.followup.send(f"❌ 오류: {e}", ephemeral=True)

    # ==========================================
    # 기능 3: /ip변경
    # ==========================================
    @discord.slash_command(name="ip변경", description="신분을 세탁합니다.")
    async def change_ip(self, ctx: discord.ApplicationContext):
        current_salt = self.user_salts.get(ctx.author.id, 0)
        self.user_salts[ctx.author.id] = current_salt + 1
        new_ip = self.get_anon_id(ctx.author.id)
        await ctx.respond(f"🔄 **신분 세탁 완료!** 새 IP: `{new_ip}`", ephemeral=True)

def setup(bot):
    bot.add_cog(AnonymousChat(bot))