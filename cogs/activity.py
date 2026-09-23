"""FR-2 닉네임 활동 관리 (마지막 활동일 + N일 카운트).

- 만료일 = **마지막 활동일 + config.ACTIVITY_PERIOD_DAYS**. 개월에서 차감하지 않는다.
- 지정 역할(config: ACTIVITY_ADMIN_ROLE_ID / ACTIVITY_ADMIN_ROLE_NAME)만 명령어 실행 가능
- FR-1 `/크레딧` 제출 시 `apply_credit_activity()` 로 자동 갱신
- 조회·신호등·공지의 기준은 '마지막 활동일 = 1일째' 로 세는 활동 카운트(+N일)
- 매일 1회 카운트가 ACTIVITY_ORANGE_DAYS 를 넘긴 멤버를 지정 채널(ACTIVITY_NOTIFY_CHANNEL_ID)에
  공지하고 warned=1 로 마킹 (개인 DM 도, 당사자 멘션도 보내지 않는다 — 채널 공지만 남긴다)
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field

import discord
from discord.ext import commands, tasks

import config
from services.activity_db import (
    ActivityDB,
    NicknameTakenError,
    elapsed_days,
    elapsed_light,
    format_elapsed_ko,
    now_kst,
    remaining_breakdown,
)
from services.credit_format import CreditPerson

logger = logging.getLogger(__name__)

DENY_MESSAGE = "❌ 이 명령어를 실행할 권한이 없습니다. (활동 관리 역할 전용)"
_EMBED_DESC_LIMIT = 3800  # Embed description 4096 제한에 여유를 둔 값
_LIGHT_COLORS = {"🟢": 0x2ECC71, "🟠": 0xE67E22, "🔴": 0xE74C3C}


def is_activity_admin(user: discord.abc.User | discord.Member) -> bool:
    """활동 관리 역할 보유 여부. 역할 ID 우선, 없으면 역할 이름으로 판정."""
    roles = getattr(user, "roles", None)
    if not roles:
        return False
    if config.ACTIVITY_ADMIN_ROLE_ID is not None and any(
        role.id == config.ACTIVITY_ADMIN_ROLE_ID for role in roles
    ):
        return True
    if config.ACTIVITY_ADMIN_ROLE_NAME and any(
        role.name == config.ACTIVITY_ADMIN_ROLE_NAME for role in roles
    ):
        return True
    return False


def require_activity_admin():
    """활동 관리 명령어 공용 check 데코레이터. 거부 시 ephemeral 응답 후 False."""

    async def predicate(ctx: discord.ApplicationContext) -> bool:
        if is_activity_admin(ctx.author):
            return True
        logger.info(
            "권한 거부: %s(%s) 가 %s 실행 시도",
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


@dataclass
class CreditApplyResult:
    """FR-2.5 자동 갱신 결과."""

    updated: list[tuple[str, str]] = field(default_factory=list)  # (닉네임, 역할)
    # 크레딧 제출 시점에 자동으로 새로 등록된 인원.
    registered_people: list[CreditPerson] = field(default_factory=list)
    # 자동 등록에 실패한 인원(같은 닉네임을 다른 user_id 가 이미 사용 중).
    conflict_people: list[CreditPerson] = field(default_factory=list)
    # user_id 를 알 수 없어 자동 등록조차 못 한 인원(닉네임 폴백 경로).
    unknown_people: list[CreditPerson] = field(default_factory=list)

    @property
    def updated_nicknames(self) -> list[str]:
        seen: list[str] = []
        for nickname, _role in self.updated:
            if nickname not in seen:
                seen.append(nickname)
        return seen

    @property
    def registered_nicknames(self) -> list[str]:
        return [person.name for person in self.registered_people]

    @property
    def unknown(self) -> list[str]:
        """미등록 인원의 표시 이름 목록."""
        return [person.name for person in self.unknown_people]


class ActivityTracker(commands.Cog):
    """활동 기간 관리 Cog."""

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.db = ActivityDB(config.ACTIVITY_DB_PATH)
        self.days = config.ACTIVITY_PERIOD_DAYS
        self._schema_ready = False
        self.expiration_scan.start()

    def cog_unload(self):
        self.expiration_scan.cancel()

    async def cog_command_error(
        self, ctx: discord.ApplicationContext, error: discord.DiscordException
    ):
        # predicate 에서 이미 ephemeral 로 안내했으므로 CheckFailure 는 조용히 흘린다.
        if isinstance(error, commands.CheckFailure):
            return
        logger.exception(
            "활동 관리 명령어 오류 (%s)", getattr(ctx.command, "qualified_name", "?"),
            exc_info=error,
        )
        message = "❌ 명령어 처리 중 오류가 발생했습니다. 로그를 확인해주세요."
        try:
            if ctx.response.is_done():
                await ctx.followup.send(message, ephemeral=True)
            else:
                await ctx.respond(message, ephemeral=True)
        except discord.HTTPException as exc:
            logger.warning("오류 응답 실패: %s", exc)

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        await self.db.init_schema()
        self._schema_ready = True

    # ------------------------------------------------------------------ #
    # FR-1 연동 (credit Cog 가 bot.get_cog 으로 호출)
    # ------------------------------------------------------------------ #
    async def apply_credit_activity(
        self, people_by_role: dict[str, list[CreditPerson]], video_title: str
    ) -> CreditApplyResult:
        """크레딧 제출 인원의 활동을 갱신한다.

        `CreditPerson.user_id` 가 있으면 ID 로 매칭한다(크레딧 모달의 유저 선택 경로).
        없으면 닉네임 문자열로 폴백하므로 예전 방식의 호출도 그대로 동작한다.

        아직 등록되지 않은 인원은 **자동으로 등록**한 뒤 갱신까지 진행한다. 크레딧에
        올랐다는 것 자체가 활동의 증거이므로, `/활동등록` 을 따로 하지 않아도 된다.
        단 user_id 를 모르면(닉네임 폴백 경로) 등록할 수 없으므로 미등록으로 남긴다.
        """
        await self._ensure_schema()
        result = CreditApplyResult()
        now = now_kst()
        seen_unknown: set[tuple[int | None, str]] = set()
        seen_registered: set[int] = set()
        conflicted: set[int] = set()

        for role, people in people_by_role.items():
            for person in people:
                name = person.name.strip()
                if not name and person.user_id is None:
                    continue
                try:
                    if person.user_id is not None:
                        record = await self.db.touch_by_user_id(
                            user_id=person.user_id,
                            video_title=video_title,
                            role_in_video=role,
                            days=self.days,
                            now=now,
                        )
                    else:
                        record = await self.db.touch_by_nickname(
                            nickname=name,
                            video_title=video_title,
                            role_in_video=role,
                            days=self.days,
                            now=now,
                        )
                except Exception:
                    logger.exception(
                        "활동 자동 갱신 실패 (이름=%r, user_id=%s, 역할=%s)",
                        name,
                        person.user_id,
                        role,
                    )
                    continue

                if record is None and person.user_id is not None and name:
                    # 미등록이면 자동 등록 후 다시 갱신해 credit_logs 까지 남긴다.
                    record = await self._auto_register(
                        person,
                        name,
                        role,
                        video_title,
                        now,
                        result,
                        seen_registered,
                        conflicted,
                    )
                    if record is None and person.user_id in conflicted:
                        # 닉네임 충돌은 이미 별도로 안내하므로 미등록으로 또 세지 않는다.
                        continue

                if record is None:
                    key = (person.user_id, name)
                    if key not in seen_unknown:
                        seen_unknown.add(key)
                        result.unknown_people.append(person)
                else:
                    # 표시는 DB 에 등록된 닉네임 기준으로 통일한다.
                    result.updated.append((record.nickname, role))
                    logger.info(
                        "활동 자동 갱신: %s(%s) 역할=%s 만료=%s",
                        record.nickname,
                        record.user_id,
                        role,
                        record.expire_date.date(),
                    )
        return result

    async def _auto_register(
        self,
        person: CreditPerson,
        name: str,
        role: str,
        video_title: str,
        now,
        result: CreditApplyResult,
        seen_registered: set[int],
        conflicted: set[int],
    ):
        """크레딧에 오른 미등록 인원을 자동 등록하고 활동을 갱신한다.

        같은 닉네임을 다른 user_id 가 선점하고 있으면 등록하지 않고 충돌로 보고한다
        (임의로 다른 닉네임을 붙이면 나중에 사람이 구분하기 더 어려워진다).
        """
        try:
            created = await self.db.register(
                user_id=person.user_id, nickname=name, days=self.days, now=now
            )
        except NicknameTakenError as exc:
            logger.warning(
                "자동 등록 실패 - 닉네임 중복: %r 은 이미 user_id=%s 가 사용 중 (요청 user_id=%s)",
                name,
                exc.owner_id,
                person.user_id,
            )
            if person.user_id not in conflicted:
                conflicted.add(person.user_id)
                result.conflict_people.append(person)
            return None
        except Exception:
            logger.exception("자동 등록 실패 (이름=%r, user_id=%s)", name, person.user_id)
            return None

        if created and person.user_id not in seen_registered:
            seen_registered.add(person.user_id)
            result.registered_people.append(person)
            logger.info("크레딧 자동 등록: %s(%s)", name, person.user_id)

        # 등록만으로는 credit_logs 가 남지 않으므로 갱신 경로를 한 번 더 태운다.
        return await self.db.touch_by_user_id(
            user_id=person.user_id,
            video_title=video_title,
            role_in_video=role,
            days=self.days,
            now=now,
        )

    # ------------------------------------------------------------------ #
    # 공용 헬퍼
    # ------------------------------------------------------------------ #
    @staticmethod
    def _light(elapsed: int) -> str:
        return elapsed_light(elapsed, config.ACTIVITY_ORANGE_DAYS, config.ACTIVITY_RED_DAYS)

    def _member_label(self, user_id: int, nickname: str) -> str:
        member = None
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member:
                break
        if member:
            return f"{nickname} ({member.display_name})"
        return f"{nickname} (ID: {user_id})"

    async def _send_chunked(
        self, ctx: discord.ApplicationContext, title: str, lines: list[str]
    ) -> None:
        """줄 목록을 Embed description 한도에 맞춰 여러 Embed 로 나눠 전송."""
        chunks: list[str] = []
        buffer = ""
        for line in lines:
            if len(buffer) + len(line) + 1 > _EMBED_DESC_LIMIT:
                chunks.append(buffer)
                buffer = ""
            buffer += line + "\n"
        if buffer:
            chunks.append(buffer)
        if not chunks:
            chunks = ["(없음)"]

        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            embed = discord.Embed(
                title=f"{title} ({index}/{total})" if total > 1 else title,
                description=chunk,
                color=0x5865F2,
            )
            await ctx.followup.send(embed=embed)

    # ------------------------------------------------------------------ #
    # FR-2.3 명령어
    # ------------------------------------------------------------------ #
    @discord.slash_command(name="활동조회", description="멤버의 잔여 활동 기간을 조회합니다.")
    @require_activity_admin()
    async def activity_lookup(
        self,
        ctx: discord.ApplicationContext,
        멤버: discord.Member = discord.Option(discord.Member, description="조회할 멤버"),
    ):
        await ctx.defer(ephemeral=True)
        await self._ensure_schema()
        record = await self.db.get(멤버.id)
        if record is None:
            return await ctx.followup.send(
                f"❌ {멤버.mention} 은(는) 등록되어 있지 않습니다. `/활동등록` 을 먼저 실행하세요.",
                ephemeral=True,
            )

        elapsed = record.elapsed()
        light = self._light(elapsed)
        embed = discord.Embed(
            title=f"{light} {record.nickname} 활동 현황",
            color=_LIGHT_COLORS[light],
        )
        embed.add_field(name="멤버", value=멤버.mention, inline=True)
        embed.add_field(name="크레딧 닉네임", value=record.nickname, inline=True)
        # 카운트는 '마지막 활동으로부터 +N일' 로만 보여준다(잔여 일수 카운트다운은 쓰지 않는다).
        embed.add_field(
            name="마지막 활동으로부터",
            value=f"{light} {format_elapsed_ko(elapsed)}",
            inline=False,
        )
        embed.add_field(
            name="마지막 활동",
            value=record.last_active.strftime("%Y-%m-%d"),
            inline=True,
        )
        embed.add_field(
            name="상태",
            value=f"{record.status}{' / 알림 발송됨' if record.warned else ''}",
            inline=True,
        )
        await ctx.followup.send(embed=embed, ephemeral=True)

    @discord.slash_command(
        name="활동전체조회", description="전체 멤버를 마지막 활동이 오래된 순으로 조회합니다."
    )
    @require_activity_admin()
    async def activity_lookup_all(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        await self._ensure_schema()
        records = await self.db.list_all()
        if not records:
            return await ctx.followup.send("등록된 멤버가 없습니다.")

        now = now_kst()
        records.sort(key=lambda record: record.last_active)
        lines = []
        for index, record in enumerate(records, start=1):
            elapsed = elapsed_days(record.last_active, now)
            lines.append(
                f"{self._light(elapsed)} **{index}. {self._member_label(record.user_id, record.nickname)}**\n"
                f"　└ 마지막 활동 {record.last_active.strftime('%Y-%m-%d')}"
                f" · {format_elapsed_ko(elapsed)}"
            )
        await self._send_chunked(
            ctx,
            f"📊 활동 현황 (총 {len(records)}명, 오래된 활동순)",
            lines + [
                "",
                f"🟢 {config.ACTIVITY_ORANGE_DAYS}일 이하 · 🟠 {config.ACTIVITY_ORANGE_DAYS}일 초과"
                f" · 🔴 {config.ACTIVITY_RED_DAYS}일 초과",
            ],
        )

    @discord.slash_command(name="활동등록", description="신규 멤버를 등록하고 카운트를 시작합니다.")
    @require_activity_admin()
    async def activity_register(
        self,
        ctx: discord.ApplicationContext,
        멤버: discord.Member = discord.Option(discord.Member, description="등록할 멤버"),
        닉네임: str = discord.Option(str, description="크레딧 표기용 닉네임 (크레딧 입력과 정확히 일치해야 함)"),
    ):
        await ctx.defer(ephemeral=True)
        await self._ensure_schema()
        try:
            created = await self.db.register(멤버.id, 닉네임, self.days)
        except NicknameTakenError as exc:
            return await ctx.followup.send(
                f"❌ 닉네임 `{닉네임.strip()}` 은 이미 <@{exc.owner_id}> 에게 등록되어 있습니다.",
                ephemeral=True,
            )

        if not created:
            existing = await self.db.get(멤버.id)
            return await ctx.followup.send(
                f"❌ {멤버.mention} 은(는) 이미 등록되어 있습니다."
                f" (닉네임: `{existing.nickname if existing else '?'}`)\n"
                "기간을 다시 시작하려면 `/활동갱신`, 닉네임만 바꾸려면 `/활동삭제` 후 재등록하세요.",
                ephemeral=True,
            )

        record = await self.db.get(멤버.id)
        if record is None:  # INSERT 직후이므로 정상 경로에서는 발생하지 않는다.
            logger.error("등록 직후 조회 실패: user_id=%s", 멤버.id)
            return await ctx.followup.send(
                "❌ 등록은 되었으나 조회에 실패했습니다. `/활동조회` 로 확인해주세요.", ephemeral=True
            )
        logger.info("활동 등록: %s(%s) 닉네임=%s", 멤버, 멤버.id, record.nickname)
        await ctx.followup.send(
            f"✅ {멤버.mention} 등록 완료\n"
            f"· 크레딧 닉네임: `{record.nickname}`\n"
            f"· 만료일: **{record.expire_date.strftime('%Y-%m-%d')}** (오늘 +{self.days}일)",
            ephemeral=True,
        )

    @discord.slash_command(name="활동갱신", description="오늘을 마지막 활동일로 찍고 기간을 다시 시작합니다.")
    @require_activity_admin()
    async def activity_renew(
        self,
        ctx: discord.ApplicationContext,
        멤버: discord.Member = discord.Option(discord.Member, description="갱신할 멤버"),
    ):
        await ctx.defer(ephemeral=True)
        await self._ensure_schema()
        record = await self.db.renew(멤버.id, self.days)
        if record is None:
            return await ctx.followup.send(
                f"❌ {멤버.mention} 은(는) 등록되어 있지 않습니다.", ephemeral=True
            )
        logger.info("활동 갱신: %s(%s) → %s", 멤버, 멤버.id, record.expire_date.date())
        await ctx.followup.send(
            f"🔄 {멤버.mention} 갱신 완료 · 만료일 **{record.expire_date.strftime('%Y-%m-%d')}**",
            ephemeral=True,
        )

    @discord.slash_command(name="활동설정", description="잔여 일수를 임의로 지정합니다.")
    @require_activity_admin()
    async def activity_set(
        self,
        ctx: discord.ApplicationContext,
        멤버: discord.Member = discord.Option(discord.Member, description="대상 멤버"),
        일수: int = discord.Option(int, description="오늘부터의 잔여 일수", min_value=0, max_value=3650),
    ):
        await ctx.defer(ephemeral=True)
        await self._ensure_schema()
        record = await self.db.set_days(멤버.id, 일수)
        if record is None:
            return await ctx.followup.send(
                f"❌ {멤버.mention} 은(는) 등록되어 있지 않습니다.", ephemeral=True
            )
        logger.info("활동 설정: %s(%s) → %d일 (%s)", 멤버, 멤버.id, 일수, record.expire_date.date())
        await ctx.followup.send(
            f"⚙️ {멤버.mention} 잔여 **{일수}일** 설정 완료"
            f" · 만료일 **{record.expire_date.strftime('%Y-%m-%d')}**",
            ephemeral=True,
        )

    @discord.slash_command(name="활동초기화", description="카운트를 오늘 기준으로 초기화합니다.")
    @require_activity_admin()
    async def activity_reset(
        self,
        ctx: discord.ApplicationContext,
        멤버: discord.Member = discord.Option(discord.Member, description="초기화할 멤버"),
    ):
        await ctx.defer(ephemeral=True)
        await self._ensure_schema()
        record = await self.db.renew(멤버.id, self.days)
        if record is None:
            return await ctx.followup.send(
                f"❌ {멤버.mention} 은(는) 등록되어 있지 않습니다.", ephemeral=True
            )
        logger.info("활동 초기화: %s(%s) → %s", 멤버, 멤버.id, record.expire_date.date())
        await ctx.followup.send(
            f"♻️ {멤버.mention} 초기화 완료 (알림 플래그 해제)"
            f" · 만료일 **{record.expire_date.strftime('%Y-%m-%d')}**",
            ephemeral=True,
        )

    @discord.slash_command(name="활동삭제", description="멤버를 활동 관리 DB에서 제거합니다.")
    @require_activity_admin()
    async def activity_delete(
        self,
        ctx: discord.ApplicationContext,
        멤버: discord.Member = discord.Option(discord.Member, description="삭제할 멤버"),
    ):
        await ctx.defer(ephemeral=True)
        await self._ensure_schema()
        deleted = await self.db.delete(멤버.id)
        if not deleted:
            return await ctx.followup.send(
                f"❌ {멤버.mention} 은(는) 등록되어 있지 않습니다.", ephemeral=True
            )
        logger.info("활동 삭제: %s(%s)", 멤버, 멤버.id)
        await ctx.followup.send(f"🗑️ {멤버.mention} 을(를) DB에서 제거했습니다.", ephemeral=True)

    # ------------------------------------------------------------------ #
    # FR-2.7 (선택 구현)
    # ------------------------------------------------------------------ #
    @discord.slash_command(name="활동내보내기", description="전체 명단을 CSV 파일로 내보냅니다.")
    @require_activity_admin()
    async def activity_export(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        await self._ensure_schema()
        records = await self.db.list_all()
        if not records:
            return await ctx.followup.send("등록된 멤버가 없습니다.", ephemeral=True)

        now = now_kst()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["user_id", "nickname", "last_active", "elapsed_days", "expire_date", "remaining_days", "warned", "status"]
        )
        for record in records:
            writer.writerow(
                [
                    record.user_id,
                    record.nickname,
                    record.last_active.strftime("%Y-%m-%d"),
                    elapsed_days(record.last_active, now),
                    record.expire_date.strftime("%Y-%m-%d"),
                    remaining_breakdown(record.expire_date, now).total_days,
                    int(record.warned),
                    record.status,
                ]
            )
        # Excel 에서 한글이 깨지지 않도록 BOM 포함 UTF-8 로 인코딩
        data = io.BytesIO(buffer.getvalue().encode("utf-8-sig"))
        filename = f"activity_{now.strftime('%Y%m%d')}.csv"
        await ctx.followup.send(
            f"📄 총 {len(records)}명 명단입니다.",
            file=discord.File(fp=data, filename=filename),
            ephemeral=True,
        )

    @discord.slash_command(name="활동이력", description="멤버의 크레딧 참여 이력을 조회합니다.")
    @require_activity_admin()
    async def activity_history(
        self,
        ctx: discord.ApplicationContext,
        멤버: discord.Member = discord.Option(discord.Member, description="조회할 멤버"),
        개수: int = discord.Option(int, description="최근 몇 건 (기본 10)", required=False, default=10, min_value=1, max_value=25),
    ):
        await ctx.defer(ephemeral=True)
        await self._ensure_schema()
        record = await self.db.get(멤버.id)
        if record is None:
            return await ctx.followup.send(
                f"❌ {멤버.mention} 은(는) 등록되어 있지 않습니다.", ephemeral=True
            )
        logs = await self.db.list_credit_logs(멤버.id, limit=개수)
        if not logs:
            return await ctx.followup.send(
                f"`{record.nickname}` 의 크레딧 참여 이력이 없습니다.", ephemeral=True
            )

        embed = discord.Embed(
            title=f"🎬 {record.nickname} 크레딧 참여 이력 (최근 {len(logs)}건)",
            color=0x5865F2,
            description="\n".join(
                f"· `{log.submitted_at.strftime('%Y-%m-%d')}` **{log.role_in_video}** — {log.video_title}"
                for log in logs
            ),
        )
        await ctx.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # FR-2.6 자동 알림
    # ------------------------------------------------------------------ #
    @tasks.loop(hours=24)
    async def expiration_scan(self):
        """매일 활동 카운트가 기준을 넘긴 멤버를 스캔해 알림하고 warned=1 로 마킹."""
        try:
            await self._ensure_schema()
            pending = await self.db.list_pending_warnings(config.ACTIVITY_ORANGE_DAYS)
            await self.db.mark_expired()
            if not pending:
                logger.info("활동 공지 대상 없음 (기준 %d일 초과)", config.ACTIVITY_ORANGE_DAYS)
                return

            channel = await self._get_notify_channel()
            if channel is None:
                logger.error(
                    "ACTIVITY_NOTIFY_CHANNEL_ID(%s) 채널을 찾을 수 없어 알림을 보내지 못했습니다."
                    " warned 플래그는 유지합니다.",
                    config.ACTIVITY_NOTIFY_CHANNEL_ID,
                )
                return

            now = now_kst()
            lines = []
            for record in pending:
                elapsed = elapsed_days(record.last_active, now)
                # 멘션(<@id>)도 DM 도 쓰지 않는다. 채널에 이름만 적어 공지로 남긴다.
                lines.append(
                    f"{self._light(elapsed)} **{record.nickname}** 님이 마지막 활동으로부터"
                    f" **+{elapsed}일째**입니다! (마지막 활동 {record.last_active.strftime('%Y-%m-%d')})"
                )

            embed = discord.Embed(
                title=f"⏰ 활동 {config.ACTIVITY_ORANGE_DAYS}일 초과 ({len(pending)}명)",
                description="\n".join(lines)[:_EMBED_DESC_LIMIT],
                color=_LIGHT_COLORS["🟠"],
            )
            embed.set_footer(
                text=f"🟠 {config.ACTIVITY_ORANGE_DAYS}일 초과 · 🔴 {config.ACTIVITY_RED_DAYS}일 초과"
                " · 영상 크레딧에 오르면 카운트가 +1일로 초기화됩니다"
            )
            # 닉네임에 멘션처럼 보이는 문자열이 들어가도 실제 알림이 가지 않도록 막는다.
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

            await self.db.mark_warned([record.user_id for record in pending])
            logger.info("활동 %d일 초과 공지 발송 완료: %d명", config.ACTIVITY_ORANGE_DAYS, len(pending))
        except Exception:
            # loop 안에서 예외가 새면 태스크가 죽으므로 반드시 잡아서 기록한다.
            logger.exception("활동 공지 스캔 중 오류")

    async def _get_notify_channel(self):
        channel_id = config.ACTIVITY_NOTIFY_CHANNEL_ID
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                logger.error("알림 채널 조회 실패 (%s): %s", channel_id, exc)
                return None
        return channel

    @expiration_scan.before_loop
    async def before_expiration_scan(self):
        await self.bot.wait_until_ready()


def setup(bot: discord.Bot):
    bot.add_cog(ActivityTracker(bot))
