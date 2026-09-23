"""신입 OT 수요조사 공지 + 반응 기반 일정 자동 등록.

흐름은 이렇다.

1. 매주 수요일 14:00(KST) 에 **매번 같은 문구**의 수요조사를 올리고, 봇이 참가 표시용
   이모지를 미리 달아둔다(`weekly_notice`).
2. 그 이모지를 누른 사람 중 **OT 를 한 번도 안 들은 사람이 1명이라도 있으면** 같은 날
   21:00 로 OT 일정이 자동 등록된다(`/일정` 과 같은 저장소를 쓴다).
3. 일정 멘션 대상은 **이모지를 누른 사람 전원**이다 — 미참가자든 이미 들은 사람이든
   누른 사람은 그날 OT 에 오는 사람이기 때문이다. 다만 **열지 말지**를 정하는 건
   미참가자뿐이라, 이미 들은 사람만 눌렀다면 일정은 잡히지 않는다.
4. 일정 시각이 되면(`on_schedule_fired`) 누른 사람들을 '참가함' 으로 찍는다. 다음 주부터
   그 사람들만으로는 일정이 열리지 않는다.

참가 이력은 `services/ot_db.py` 가 들고 있고, 판정은 "행이 없으면 미참가" 다.
그래서 **최초 1회** 서버 인원을 스캔해 기존 인원을 참가함으로 찍어두면(`_seed_once`),
그 뒤에 들어오는 사람은 자동으로 OT 대상이 된다.

반응은 취소될 수도 있고 봇이 꺼져 있는 동안 눌릴 수도 있으므로, 이벤트마다 개별 처리
하지 않고 **메시지의 현재 반응 목록을 다시 읽어 일정 상태를 맞추는**(`_sync_participants`)
한 가지 경로로만 반영한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import discord
from discord.ext import commands, tasks

import config
from cogs.activity import require_activity_admin
from cogs.admin import require_developer
from services.ot_db import (
    META_NOTICE_CHANNEL_ID,
    META_NOTICE_DATE,
    META_NOTICE_MESSAGE_ID,
    SOURCE_MANUAL,
    SOURCE_OT,
    OtAttendanceDB,
)

logger = logging.getLogger(__name__)

KST = dt.timezone(dt.timedelta(hours=9))
WEEKDAY_NAMES = ("월", "화", "수", "목", "금", "토", "일")
SCHEDULE_KIND = "ot"  # 일정 dict 에 남기는 꼬리표 (on_schedule_fired 에서 구분)
LIST_LIMIT = 40  # /ot명단 에 한 번에 보여줄 인원 수

# 루프 시각은 Cog 로드 시점에 고정된다(데코레이터 인자라 재시작해야 바뀐다).
NOTICE_TIME = dt.time(
    hour=config.OT_NOTICE_HOUR, minute=config.OT_NOTICE_MINUTE, tzinfo=KST
)


def schedule_id_for(day: dt.date) -> str:
    """그날의 OT 일정 id. 같은 날 두 번 만들지 않기 위해 날짜로 고정한다."""
    return f"ot-{day.isoformat()}"


def mentions_of(user_ids: list[int]) -> str:
    return " ".join(f"<@{user_id}>" for user_id in user_ids)


class OtNotice(commands.Cog):
    """신입 OT 수요조사 공지와 그 반응에 따른 일정 등록."""

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.db = OtAttendanceDB(config.OT_DB_PATH)
        # 리로드 직후 같은 날 두 번 올라가는 일을 막는 최소한의 방어.
        self._last_sent_date: dt.date | None = None
        self._ready_done = False
        # 반응이 연달아 들어와도 일정이 두 번 만들어지지 않게 한 번에 하나씩 처리한다.
        self._sync_lock = asyncio.Lock()
        self._notice_emoji = (
            discord.PartialEmoji.from_str(config.OT_NOTICE_EMOJI)
            if config.OT_NOTICE_EMOJI
            else None
        )

        if config.OT_NOTICE_CHANNEL_ID is None:
            logger.warning(
                "OT_NOTICE_CHANNEL_ID 가 없어 신입 OT 수요조사 공지를 보낼 수 없습니다."
            )
            return

        self.weekly_notice.start()
        logger.info(
            "신입 OT 수요조사 공지 예약: 매주 %s요일 %02d:%02d (KST) → 채널 %s"
            " · 미참가자 반응 시 당일 %02d:%02d 일정 등록",
            WEEKDAY_NAMES[config.OT_NOTICE_WEEKDAY % 7],
            config.OT_NOTICE_HOUR,
            config.OT_NOTICE_MINUTE,
            config.OT_NOTICE_CHANNEL_ID,
            config.OT_SCHEDULE_HOUR,
            config.OT_SCHEDULE_MINUTE,
        )

    def cog_unload(self):
        self.weekly_notice.cancel()

    def _now(self) -> dt.datetime:
        # 테스트에서 시각을 갈아끼울 수 있도록 한 곳으로 모아 둔다.
        return dt.datetime.now(KST)

    # ------------------------------------------------------------------ #
    # 기동 시 1회: 스키마 · 기존 인원 스캔 · 놓친 반응 반영
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_ready(self):
        if self._ready_done:  # on_ready 는 재연결 때마다 다시 불린다.
            return
        self._ready_done = True
        try:
            await self.db.init_schema()
            await self._seed_once()
            # 봇이 꺼져 있는 동안 눌린 반응을 반영한다.
            await self._sync_participants()
        except Exception:
            logger.exception("신입 OT 초기화 중 오류")

    async def _seed_once(self) -> None:
        """서버에 현재 있는 인원을 '참가함' 으로 한 번만 찍는다.

        딱 1회만 돌기 때문에 이 시점 이후 들어온 사람은 DB 에 행이 없고, 곧 미참가로
        취급된다 — 신입이 자동으로 OT 대상이 되는 게 이 설계의 핵심이다.
        """
        if await self.db.is_seeded():
            return

        member_ids: list[int] = []
        for guild in self.bot.guilds:
            if not guild.members:  # 멤버 캐시가 비어 있으면 한 번 받아온다.
                try:
                    await guild.chunk()
                except Exception:
                    logger.exception("길드 %s 멤버 목록을 받아오지 못했습니다.", guild.id)
            member_ids.extend(member.id for member in guild.members if not member.bot)

        if not member_ids:
            logger.warning(
                "서버 인원을 한 명도 읽지 못해 OT 최초 스캔을 미룹니다(다음 기동 때 다시 시도)."
            )
            return

        attended, excluded = await self.db.seed(member_ids, config.OT_SEED_EXCLUDE_IDS)
        logger.info(
            "신입 OT 최초 스캔: 기존 인원 %d명 참가함 처리 · 예외 %d명은 미참가로 남김",
            attended,
            excluded,
        )

    # ------------------------------------------------------------------ #
    # 수요조사 공지 발송
    # ------------------------------------------------------------------ #
    async def _get_channel(self) -> discord.abc.Messageable | None:
        channel_id = config.OT_NOTICE_CHANNEL_ID
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            # 캐시에 없으면(재기동 직후 등) 한 번 직접 가져와 본다.
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException as exc:
                logger.error("OT 공지 채널(%s) 을 가져오지 못했습니다: %s", channel_id, exc)
                return None
        if not isinstance(channel, discord.abc.Messageable):
            logger.error("OT 공지 채널(%s) 에 메시지를 보낼 수 없습니다.", channel_id)
            return None
        return channel

    async def send_notice(self) -> discord.Message | None:
        """수요조사 공지를 한 번 보내고, 참가 표시용 이모지를 미리 달아준다."""
        channel = await self._get_channel()
        if channel is None:
            return None

        message = await channel.send(
            config.OT_NOTICE_MESSAGE,
            # 문구에 멘션처럼 보이는 게 들어가도 실제 알림이 가지 않게 막는다.
            allowed_mentions=discord.AllowedMentions.none(),
        )

        if config.OT_NOTICE_EMOJI:
            try:
                await message.add_reaction(config.OT_NOTICE_EMOJI)
            except discord.HTTPException as exc:
                # 이모지를 못 달아도 공지 자체는 이미 올라갔으므로 실패로 보지 않는다.
                logger.warning(
                    "OT 공지 이모지(%s) 를 달지 못했습니다: %s", config.OT_NOTICE_EMOJI, exc
                )

        # 반응을 감시할 대상은 항상 '가장 최근 공지' 하나다.
        await self.db.set_meta(META_NOTICE_MESSAGE_ID, message.id)
        await self.db.set_meta(META_NOTICE_CHANNEL_ID, message.channel.id)
        await self.db.set_meta(META_NOTICE_DATE, self._now().date().isoformat())
        return message

    # ------------------------------------------------------------------ #
    # 주간 루프
    # ------------------------------------------------------------------ #
    @tasks.loop(time=NOTICE_TIME)
    async def weekly_notice(self):
        now = self._now()
        if now.weekday() != config.OT_NOTICE_WEEKDAY:
            return
        if self._last_sent_date == now.date():
            logger.info("신입 OT 수요조사 공지는 오늘 이미 보냈습니다. 건너뜁니다.")
            return

        try:
            message = await self.send_notice()
        except Exception:
            # loop 안에서 예외가 새면 태스크가 죽으므로 반드시 잡아서 기록한다.
            logger.exception("신입 OT 수요조사 공지 발송 중 오류")
            return

        if message is not None:
            self._last_sent_date = now.date()
            logger.info("신입 OT 수요조사 공지 발송 완료 (메시지 %s)", message.id)

    @weekly_notice.before_loop
    async def before_weekly_notice(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ #
    # 반응 → 일정 등록
    # ------------------------------------------------------------------ #
    def _emoji_matches(self, emoji) -> bool:
        if self._notice_emoji is None:
            return False
        if self._notice_emoji.id is not None:
            return getattr(emoji, "id", None) == self._notice_emoji.id
        return str(emoji) == str(self._notice_emoji)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._on_notice_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._on_notice_reaction(payload)

    async def _on_notice_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        if self.bot.user and payload.user_id == self.bot.user.id:
            return
        if not self._emoji_matches(payload.emoji):
            return
        notice_message_id = await self.db.get_meta_int(META_NOTICE_MESSAGE_ID)
        if notice_message_id is None or payload.message_id != notice_message_id:
            return
        try:
            await self._sync_participants()
        except Exception:
            logger.exception("OT 수요조사 반응 처리 중 오류")

    async def _fetch_notice_message(self) -> discord.Message | None:
        message_id = await self.db.get_meta_int(META_NOTICE_MESSAGE_ID)
        channel_id = await self.db.get_meta_int(META_NOTICE_CHANNEL_ID)
        if message_id is None or channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        try:
            return await channel.fetch_message(message_id)
        except discord.HTTPException:
            # 공지가 지워졌으면 더 볼 게 없다.
            logger.info("OT 수요조사 공지(%s) 를 찾을 수 없습니다.", message_id)
            return None

    async def _reacted_user_ids(self, message: discord.Message) -> list[int]:
        for reaction in message.reactions:
            if not self._emoji_matches(reaction.emoji):
                continue
            return [user.id async for user in reaction.users() if not user.bot]
        return []

    async def _sync_participants(self) -> dict | None:
        """공지의 현재 반응을 읽어 OT 일정을 만들거나 고치거나 지운다.

        반응을 취소하는 경우, 봇이 꺼진 사이 눌린 경우까지 이 한 경로로 처리된다.
        """
        async with self._sync_lock:
            raw_date = await self.db.get_meta(META_NOTICE_DATE)
            if not raw_date:
                return None
            notice_date = dt.date.fromisoformat(raw_date)
            today = self._now().date()
            if notice_date != today:
                # 지난 주 공지에 뒤늦게 눌린 반응으로 과거 일정을 만들지 않는다.
                return None

            message = await self._fetch_notice_message()
            if message is None:
                return None

            # 멘션 대상은 반응을 누른 사람 전원(미참가자 + 이미 들은 사람)이다.
            # 다만 **진행 여부**는 미참가자가 한 명이라도 있는지로만 판단한다.
            participants = await self._reacted_user_ids(message)
            attended = await self.db.attended_user_ids()
            pending = [user_id for user_id in participants if user_id not in attended]

            events = self.bot.get_cog("EventListener")
            if events is None:
                logger.error("EventListener Cog 를 찾을 수 없어 OT 일정을 등록하지 못했습니다.")
                return None

            sid = schedule_id_for(notice_date)
            existing = events.find_schedule(sid)

            if not pending:
                if existing is not None:
                    events.remove_schedule(sid)
                    logger.info(
                        "미참가 반응자가 남아 있지 않아 OT 일정을 지웠습니다(반응자 %d명).",
                        len(participants),
                    )
                return None

            if existing is not None:
                if existing.get("participants") == participants:
                    return existing
                existing["participants"] = participants
                existing["mention"] = mentions_of(participants)
                events.save_schedules()
                logger.info(
                    "OT 일정 참가자 갱신: 반응자 %d명 %s (그중 미참가 %d명)",
                    len(participants),
                    participants,
                    len(pending),
                )
                return existing

            target_time = dt.datetime.combine(
                notice_date,
                dt.time(config.OT_SCHEDULE_HOUR, config.OT_SCHEDULE_MINUTE),
                tzinfo=KST,
            )
            if target_time <= self._now():
                logger.warning(
                    "OT 시각(%s) 이 이미 지나 일정을 만들지 않았습니다. 필요하면 /일정 으로 직접 등록하세요.",
                    target_time.strftime("%Y-%m-%d %H:%M"),
                )
                return None

            schedule = await events.add_schedule(
                target_time=target_time,
                content=config.OT_SCHEDULE_CONTENT,
                mention=mentions_of(participants),
                author="신입 OT 수요조사",
                schedule_id=sid,
                kind=SCHEDULE_KIND,
                participants=participants,
            )
            logger.info(
                "OT 일정 자동 등록: %s / 반응자 %d명 %s (그중 미참가 %d명 %s)",
                target_time.strftime("%Y-%m-%d %H:%M"),
                len(participants),
                participants,
                len(pending),
                pending,
            )
            return schedule

    # ------------------------------------------------------------------ #
    # 일정이 시작되면 참가 처리
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_schedule_fired(self, schedule: dict):
        if schedule.get("kind") != SCHEDULE_KIND:
            return
        participants = [int(user_id) for user_id in schedule.get("participants") or []]
        if not participants:
            return
        await self.db.set_attended_many(participants, True, SOURCE_OT)
        logger.info(
            "OT 진행에 따라 참가 처리: %d명 %s (되돌리려면 /ot참가처리)",
            len(participants),
            participants,
        )

    # ------------------------------------------------------------------ #
    # 운영 명령어
    # ------------------------------------------------------------------ #
    @discord.slash_command(
        name="ot공지",
        description="신입 OT 수요조사 공지를 지금 한 번 보냅니다 (개발자 전용)",
    )
    @require_developer()
    async def ot_notice_now(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        try:
            message = await self.send_notice()
        except discord.HTTPException as exc:
            return await ctx.followup.send(f"❌ 공지 발송 실패: {exc}", ephemeral=True)
        if message is None:
            return await ctx.followup.send(
                f"❌ OT 공지 채널({config.OT_NOTICE_CHANNEL_ID}) 을 찾지 못했습니다.",
                ephemeral=True,
            )
        await ctx.followup.send(f"✅ 공지를 보냈습니다: {message.jump_url}", ephemeral=True)

    @discord.slash_command(
        name="ot명단", description="아직 신입 OT 를 안 들은 인원을 확인합니다."
    )
    @require_activity_admin()
    async def ot_pending(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        attended = await self.db.attended_user_ids()
        members = [
            member
            for member in (ctx.guild.members if ctx.guild else [])
            if not member.bot and member.id not in attended
        ]
        if not members:
            return await ctx.followup.send(
                "✅ 서버 인원 모두 신입 OT 를 들은 것으로 기록돼 있습니다.", ephemeral=True
            )

        members.sort(key=lambda m: m.display_name)
        shown = members[:LIST_LIMIT]
        lines = [f"· {member.display_name} (`{member.id}`)" for member in shown]
        if len(members) > LIST_LIMIT:
            lines.append(f"…외 {len(members) - LIST_LIMIT}명")
        embed = discord.Embed(
            title=f"🐣 신입 OT 미참가 {len(members)}명",
            description="\n".join(lines),
            color=0xFEE75C,
        )
        embed.set_footer(text="수요조사에 이 중 한 명이라도 반응하면 그날 OT 일정이 잡힙니다.")
        await ctx.followup.send(embed=embed, ephemeral=True)

    @discord.slash_command(
        name="ot참가처리", description="특정 멤버의 신입 OT 참가 여부를 직접 지정합니다."
    )
    @require_activity_admin()
    async def ot_set_attended(
        self,
        ctx: discord.ApplicationContext,
        멤버: discord.Member = discord.Option(discord.Member, description="대상 멤버"),
        참가여부: bool = discord.Option(
            bool, description="참가함(True) / 미참가(False)", default=True
        ),
    ):
        await self.db.set_attended(멤버.id, 참가여부, SOURCE_MANUAL)
        state = "참가함 ✅" if 참가여부 else "미참가 🐣"
        logger.info(
            "OT 참가 여부 수동 변경: %s(%s) → %s (실행 %s)",
            멤버.display_name,
            멤버.id,
            state,
            ctx.author,
        )
        await ctx.respond(
            f"**{멤버.display_name}** 님을 **{state}** 으로 기록했습니다.", ephemeral=True
        )


def setup(bot: discord.Bot):
    bot.add_cog(OtNotice(bot))
