"""FR-4 개발자 전용 리로드 / 재시작 명령어.

권한은 `DEV_USER_IDS`(config/.env) 에 등록된 user_id 로만 제한한다.
`/재시작` 은 systemd 의 `Restart=on-failure` 를 전제로 하며, 종료 코드가 0이면
systemd 가 재기동하지 않으므로 **1로 종료**되도록 처리한다(deploy/discordbot.service 참고).
"""

from __future__ import annotations

import logging
import sys
import traceback

import discord
from discord.ext import commands

import config

logger = logging.getLogger(__name__)

COG_PACKAGE = "cogs"
DENY_MESSAGE = "❌ 이 명령어는 개발자 전용입니다."
TRACEBACK_LIMIT = 1200  # ephemeral 응답에 담을 traceback 최대 길이


def is_developer(user: discord.abc.User) -> bool:
    return user.id in config.DEV_USER_IDS


def require_developer():
    """개발자 전용 check 데코레이터. 거부 시 ephemeral 응답 후 False."""

    async def predicate(ctx: discord.ApplicationContext) -> bool:
        if is_developer(ctx.author):
            return True
        logger.warning(
            "개발자 전용 명령어 거부: %s(%s) → %s",
            ctx.author,
            ctx.author.id,
            getattr(ctx.command, "qualified_name", "?"),
        )
        try:
            await ctx.respond(DENY_MESSAGE, ephemeral=True)
        except discord.HTTPException as exc:
            logger.warning("권한 거부 응답 실패: %s", exc)
        return False

    return commands.check(predicate)


async def cog_autocomplete(ctx: discord.AutocompleteContext) -> list[str]:
    """현재 로드된 Cog 이름을 자동완성으로 제공한다."""
    loaded = sorted(
        name.split(".")[-1]
        for name in ctx.bot.extensions
        if name.startswith(f"{COG_PACKAGE}.")
    )
    current = (ctx.value or "").lower()
    return [name for name in loaded if current in name.lower()][:25]


class Admin(commands.Cog):
    """개발자 전용 운영 명령어."""

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        if not config.DEV_USER_IDS:
            logger.warning(
                "DEV_USER_IDS 가 비어 있어 /리로드 · /재시작 을 아무도 실행할 수 없습니다."
            )

    async def cog_command_error(
        self, ctx: discord.ApplicationContext, error: discord.DiscordException
    ):
        if isinstance(error, commands.CheckFailure):
            return  # predicate 에서 이미 안내함
        logger.exception(
            "개발자 명령어 오류 (%s)", getattr(ctx.command, "qualified_name", "?"), exc_info=error
        )

    # ------------------------------------------------------------------ #
    # FR-4.1 /리로드
    # ------------------------------------------------------------------ #
    @discord.slash_command(name="리로드", description="특정 Cog를 다시 불러옵니다. (개발자 전용)")
    @require_developer()
    async def reload_cog(
        self,
        ctx: discord.ApplicationContext,
        cog명: str = discord.Option(
            str, description="다시 불러올 Cog 이름", autocomplete=cog_autocomplete
        ),
    ):
        await ctx.defer(ephemeral=True)
        name = cog명.strip()
        extension = name if name.startswith(f"{COG_PACKAGE}.") else f"{COG_PACKAGE}.{name}"

        try:
            self.bot.reload_extension(extension)
        except discord.ExtensionNotLoaded:
            logger.warning("리로드 실패 - 로드되지 않은 확장: %s", extension)
            return await ctx.followup.send(
                f"❌ `{extension}` 은 로드되어 있지 않습니다.", ephemeral=True
            )
        except discord.ExtensionNotFound:
            logger.warning("리로드 실패 - 존재하지 않는 확장: %s", extension)
            return await ctx.followup.send(
                f"❌ `{extension}` 파일을 찾을 수 없습니다.", ephemeral=True
            )
        except (discord.ExtensionFailed, discord.NoEntryPointError) as exc:
            summary = self._format_traceback(exc)
            logger.exception("리로드 실패: %s", extension)
            return await ctx.followup.send(
                f"❌ `{extension}` 리로드 실패\n```py\n{summary}\n```", ephemeral=True
            )
        except Exception as exc:
            summary = self._format_traceback(exc)
            logger.exception("리로드 중 예상치 못한 오류: %s", extension)
            return await ctx.followup.send(
                f"❌ `{extension}` 리로드 중 예외 발생\n```py\n{summary}\n```", ephemeral=True
            )

        logger.info("Cog 리로드 완료: %s (요청자: %s)", extension, ctx.author)
        await ctx.followup.send(f"✅ `{extension}` 리로드 완료!", ephemeral=True)

    @staticmethod
    def _format_traceback(exc: BaseException) -> str:
        """ephemeral 응답에 담을 수 있게 traceback 뒷부분을 잘라 요약한다."""
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if len(text) <= TRACEBACK_LIMIT:
            return text
        return "...(생략)...\n" + text[-TRACEBACK_LIMIT:]

    # ------------------------------------------------------------------ #
    # FR-4.2 /재시작
    # ------------------------------------------------------------------ #
    @discord.slash_command(name="재시작", description="봇 프로세스를 재시작합니다. (개발자 전용)")
    @require_developer()
    async def restart(self, ctx: discord.ApplicationContext):
        await ctx.respond("♻️ 재시작합니다...", ephemeral=True)
        logger.warning("재시작 요청: %s(%s)", ctx.author, ctx.author.id)

        # systemd 의 Restart=on-failure 는 종료 코드가 0이면 재기동하지 않는다.
        # bot.close() 만 하면 bot.run() 이 정상 반환해 exit 0 이 되므로,
        # main.py 가 이 플래그를 보고 exit(1) 하도록 표시한다.
        self.bot.restart_requested = True
        await self.bot.close()
        sys.exit(1)


def setup(bot: discord.Bot):
    bot.add_cog(Admin(bot))
