"""KeepBot 진입점.

- 설정값은 전부 `config` 모듈(=.env)에서 읽는다.
- Cog 는 `cogs/` 폴더를 순회해 `cogs.<파일명>` 으로 로드한다.
- `/재시작` 이 실행되면 종료 코드 1로 빠져나가 systemd 가 재기동하게 한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import discord

import config

# py-cord 2.x 의 discord.Bot 은 **생성 시점**에 이벤트 루프를 필요로 한다.
# Python 3.12+ 는 암묵적 루프 생성이 사라졌으므로 명시적으로 준비해 둔다.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
# 디스코드 라이브러리의 상세 로그는 WARNING 이상만 남긴다.
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)

logger = logging.getLogger("keepbot")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
# FR-6: 포럼 포스트/댓글의 반응을 raw 이벤트로 받기 위해 필요하다.
# (Intents.default() 에도 포함되지만 의존하는 기능이 있으므로 명시한다.)
intents.reactions = True

bot = discord.Bot(
    intents=intents,
    auto_sync_commands=config.AUTO_SYNC_COMMANDS,
)
bot.restart_requested = False

COGS_PATH = os.path.join(config.BASE_DIR, "cogs")


def load_cogs() -> None:
    if not os.path.isdir(COGS_PATH):
        logger.error("'cogs' 폴더가 없습니다: %s", COGS_PATH)
        return

    for filename in sorted(os.listdir(COGS_PATH)):
        if not filename.endswith(".py") or filename.startswith("__"):
            continue
        extension = f"cogs.{filename[:-3]}"
        try:
            bot.load_extension(extension)
            logger.info("✅ %s 로드 완료", extension)
        except Exception:
            # 하나가 실패해도 나머지 Cog 는 계속 로드한다.
            logger.exception("❌ %s 로드 실패", extension)


@bot.event
async def on_ready():
    logger.info("--- %s 온라인 시작 ---", bot.user)
    logger.info(
        "길드 %d개 · 등록된 명령어 %d개", len(bot.guilds), len(bot.application_commands)
    )
    for guild in bot.guilds:
        logger.info("  · %s (%s)", guild.name, guild.id)


@bot.event
async def on_unknown_application_command(interaction: discord.Interaction):
    """코드에 없는 명령어가 실행된 경우. py-cord 가 이 시점에 해당 등록을 삭제한다.

    기본 로그는 DEBUG 레벨이라 discord 로거를 WARNING 으로 올린 상태에서는 보이지 않는다.
    조용히 명령어가 사라지는 걸 막기 위해 명시적으로 남긴다.
    """
    name = (interaction.data or {}).get("name", "?")
    logger.warning(
        "알 수 없는 명령어 실행: /%s (길드 %s) — 이 명령어의 등록이 삭제됩니다. "
        "코드에서 지운 명령어이거나, 예전에 등록된 채로 남아있던 것입니다.",
        name,
        interaction.guild_id,
    )


def main() -> int:
    logger.info("설정 파일: %s", config.DOTENV_PATH or "(기본 탐색)")
    for problem in config.validate():
        logger.warning("설정 확인 필요: %s", problem)

    if not config.TOKEN:
        logger.error("TOKEN 이 없어 봇을 기동할 수 없습니다. .env 를 확인하세요.")
        return 1

    load_cogs()

    try:
        bot.run(config.TOKEN)
    except discord.LoginFailure:
        logger.error("로그인 실패: TOKEN 이 올바르지 않습니다.")
        return 1

    if getattr(bot, "restart_requested", False):
        # systemd 의 Restart=on-failure 가 재기동하도록 0이 아닌 코드로 종료한다.
        logger.warning("재시작 요청에 따라 종료합니다 (exit 1).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
