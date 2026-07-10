import discord
import os
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.guild_reactions = True

bot = discord.Bot(
    intents=intents,
    # 입력하신 서버 ID (디버그용 서버)
    debug_guilds=[1472847600746233879]
)   

base_path = os.path.dirname(os.path.abspath(__file__))
cogs_path = os.path.join(base_path, 'cogs')

if os.path.exists(cogs_path):
    for filename in os.listdir(cogs_path):
        if filename.endswith('.py') and not filename.startswith('__'):
            bot.load_extension(f'cogs.{filename[:-3]}')
            print(f'✅ {filename} 로드 완료!')
else:
    print(f"❌ 에러: 'cogs' 폴더가 없습니다.")

@bot.event
async def on_ready():
    print(f"--- {bot.user} 온라인 시작 ---")

bot.run(os.getenv('TOKEN'))