"""일정 예약 알림.

입력 UI 는 `/크레딧`(cogs/credit.py) 과 같은 모달 방식이다.

    /일정 → 모달(날짜 / 날짜 직접 입력 / 시간 / 내용 / 대상) → [제출]
         → 등록 완료 + 알림 채널 공지

편의성을 위해 이렇게 구성했다.

* **날짜**는 타이핑이 아니라 선택 메뉴다. 오늘/내일/모레 + 향후 2주가 요일과 함께
  나열되고 기본값은 "오늘" 이다. 더 먼 날짜는 "직접 입력" 을 고른 뒤 아래 칸에 적는다.
* **시간**은 사람이 쓰는 대로 받는다. `오후 3시` `3시 30분` `15:30` `1530` `저녁 8시`
  `3pm` `정오` 등을 모두 해석한다(`parse_time`).
* **대상**은 멘션 선택 메뉴(`MentionableSelect`)라 멤버와 역할을 한 번에 고를 수 있다.
* 등록 결과는 디스코드 타임스탬프(`<t:...>`)로 보여준다. 보는 사람의 시간대로 자동
  변환되고 "3시간 후" 같은 상대 시간도 함께 나오므로 잘못 입력했으면 바로 눈에 띈다.
* **취소**는 번호 대신 드롭다운에서 일정을 고른다. 관리진은 전체, 그 외에는 자신이
  등록한 일정만 목록에 나온다.

선택 메뉴를 모달에 넣으려면 `discord.ui.DesignerModal` + `Label` 조합이라야 한다
(구형 `discord.ui.Modal` 은 텍스트 입력만 지원). 모달 컴포넌트는 5개가 상한이라
위 5칸으로 정확히 맞춰져 있다.

입력이 잘못되면 모달 제출 응답으로 다시 모달을 열 수 없으므로, 크레딧과 같은 방식으로
"다시 입력" 버튼이 달린 ephemeral 메시지로 응답한다. 버튼 클릭 interaction 에서 모달을
다시 열며, 이때 이전 입력값이 그대로 채워져 있다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

# 1. 한국 시간(KST) 정의
KST = timezone(timedelta(hours=9))

VIEW_TIMEOUT = 600  # 10분
MAX_PICK = 25  # 선택 메뉴 1개당 고를 수 있는 최대 대상 (디스코드 제한)
MAX_OPTIONS = 25  # 선택 메뉴 1개당 최대 항목 수 (디스코드 제한)
DATE_CHOICE_DAYS = 14  # 날짜 선택 메뉴에 미리 깔아둘 날짜 수 (오늘 포함)
CUSTOM_DATE = "custom"  # "직접 입력" 을 뜻하는 선택지 value

WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")

# 날짜: "02/17", "2/17", "2026/02/17" 모두 허용 (구분자는 . - / 및 "2월 17일")
_DATE_FORMATS = ("%m/%d", "%Y/%m/%d", "%y/%m/%d")
# 시간: "14:30", "1430", "14" 형태. 오전/오후 같은 말은 아래에서 미리 떼어낸다.
# "%H" 를 "%H%M" 보다 먼저 둔다 — 순서를 바꾸면 "13" 이 %H%M 에 1시 3분으로 걸린다.
_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%H", "%H%M")

# 시간 앞뒤에 붙는 오전/오후 표현. 긴 것부터 검사해야 "오전"이 "오"에 먼저 걸리지 않는다.
_PM_WORDS = ("오후", "저녁", "밤", "낮", "p.m.", "pm")
_AM_WORDS = ("오전", "새벽", "아침", "a.m.", "am")


def _ts(when: datetime, style: str = "F") -> str:
    """디스코드 타임스탬프 마크업. 보는 사람의 시간대로 자동 표시된다."""
    return f"<t:{int(when.timestamp())}:{style}>"


def _label_for(day: date, today: date) -> str:
    """날짜 선택지에 쓸 문구. 가까운 날은 오늘/내일/모레로 부른다."""
    delta = (day - today).days
    prefix = {0: "오늘 · ", 1: "내일 · ", 2: "모레 · "}.get(delta, "")
    return f"{prefix}{day.month}/{day.day} ({WEEKDAYS[day.weekday()]})"


def date_choices(now: datetime, selected: str | None = None) -> list[discord.SelectOption]:
    """오늘부터 2주치 날짜 + "직접 입력" 선택지를 만든다.

    `selected` 가 있으면 그 항목을 기본 선택으로 되살린다(재입력용). 없으면 오늘이
    기본값이라, 대부분의 경우 날짜는 손대지 않고 넘어갈 수 있다.
    """
    today = now.date()
    options: list[discord.SelectOption] = []
    for offset in range(DATE_CHOICE_DAYS):
        day = today + timedelta(days=offset)
        value = day.isoformat()
        options.append(
            discord.SelectOption(
                label=_label_for(day, today),
                value=value,
                default=(value == selected) if selected else (offset == 0),
            )
        )
    options.append(
        discord.SelectOption(
            label="🗓️ 직접 입력 (2주 이후)",
            value=CUSTOM_DATE,
            description="아래 '날짜 직접 입력' 칸에 MM/DD 를 적어주세요",
            default=selected == CUSTOM_DATE,
        )
    )
    return options[:MAX_OPTIONS]


def _normalize_date(raw: str) -> str:
    text = raw.strip().replace("년", "/").replace("월", "/").replace("일", "")
    text = re.sub(r"[.\-\s]+", "/", text)
    return re.sub(r"/+", "/", text).strip("/")


def parse_date(raw: str, now: datetime) -> date:
    """`02/17`, `2월 17일`, `2026-12-25` 같은 입력을 날짜로 바꾼다.

    연도를 적지 않으면 올해로 보되, 이미 하루 넘게 지난 날짜면 내년으로 넘긴다
    (12월에 1월 일정을 잡는 경우).
    """
    text = _normalize_date(raw)
    if not text:
        raise ValueError("날짜를 입력해주세요.")

    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        has_year = "%Y" in fmt or "%y" in fmt
        day = date(
            parsed.year if has_year else now.year, parsed.month, parsed.day
        )
        if not has_year and day < now.date() - timedelta(days=1):
            day = day.replace(year=day.year + 1)
        return day
    raise ValueError(f"날짜를 알아볼 수 없습니다: `{raw.strip()}`")


def parse_time(raw: str) -> tuple[int, int]:
    """`오후 3시`, `15:30`, `1530`, `저녁 8시`, `3pm`, `정오` 등을 (시, 분) 으로.

    오전/오후 표현을 먼저 떼어낸 뒤 남은 숫자만 형식에 맞춰 읽고, 마지막에 오후면
    12를 더한다. 형식을 알 수 없으면 `ValueError` 를 낸다.
    """
    text = raw.strip().lower()
    if not text:
        raise ValueError("시간을 입력해주세요.")
    if "자정" in text:
        return 0, 0
    if "정오" in text:
        return 12, 0

    is_pm = False
    is_am = False
    for word in _PM_WORDS:
        if word in text:
            is_pm = True
            text = text.replace(word, " ")
    for word in _AM_WORDS:
        if word in text:
            is_am = True
            text = text.replace(word, " ")

    text = text.replace("시", ":").replace("분", "")
    text = re.sub(r"[.\-\s]+", ":", text)
    text = re.sub(r":+", ":", text).strip(":")
    if not text:
        raise ValueError(f"시간을 알아볼 수 없습니다: `{raw.strip()}`")

    for fmt in _TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        hour, minute = parsed.hour, parsed.minute
        # "오후 3시" → 15시. 이미 24시간제로 적었으면(오후 3시 = 15시) 그대로 둔다.
        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0
        return hour, minute
    raise ValueError(f"시간을 알아볼 수 없습니다: `{raw.strip()}`")


def parse_when(date_text: str, time_text: str, now: datetime) -> datetime:
    """날짜/시간 입력을 KST `datetime` 으로 합친다."""
    day = parse_date(date_text, now)
    hour, minute = parse_time(time_text)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


def _mentions_of(picked: list) -> str:
    """선택된 멤버/역할을 멘션 문자열로 합친다(순서 유지, 중복 제거)."""
    parts: list[str] = []
    seen: set[int] = set()
    for target in picked or []:
        target_id = getattr(target, "id", None)
        if target_id is not None:
            if target_id in seen:
                continue
            seen.add(target_id)
        mention = getattr(target, "mention", None)
        if mention:
            parts.append(mention)
        elif str(target).isdigit():
            # 캐시에 없어 raw ID 로 넘어온 경우 멘션 포맷을 직접 만든다.
            parts.append(f"<@{target}>")
        else:
            parts.append(str(target))
    return " ".join(parts)


class ScheduleSession:
    """모달 입력값을 모아두는 컨테이너. 재입력 시 그대로 다시 채워 넣는다."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.raw: dict[str, str] = {}
        self.date_choice: str | None = None
        self.picked: list = []


def _item(modal: discord.ui.DesignerModal, index: int):
    """DesignerModal 의 자식은 Label 이므로 안에 든 실제 입력 컴포넌트를 꺼낸다."""
    return modal.children[index].item


def _text(modal: discord.ui.DesignerModal, index: int) -> str:
    return _item(modal, index).value or ""


def _first_value(modal: discord.ui.DesignerModal, index: int) -> str | None:
    values = _item(modal, index).values or []
    return str(values[0]) if values else None


class ScheduleModal(discord.ui.DesignerModal):
    """날짜 / 날짜 직접 입력 / 시간 / 내용 / 대상 5칸 (모달 상한)."""

    def __init__(self, cog: "EventListener", session: ScheduleSession):
        super().__init__(title="일정 등록", timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.session = session
        raw = session.raw
        self.add_item(
            discord.ui.Label(
                "날짜",
                discord.ui.StringSelect(
                    placeholder="날짜를 선택하세요",
                    options=date_choices(datetime.now(KST), session.date_choice),
                    min_values=1,
                    max_values=1,
                    required=True,
                ),
                description="기본값은 오늘입니다 · 2주 이후는 '직접 입력'",
            )
        )
        self.add_item(
            discord.ui.Label(
                "날짜 직접 입력",
                discord.ui.InputText(
                    placeholder="02/17",
                    value=raw.get("날짜 직접 입력"),
                    max_length=20,
                    required=False,
                ),
                description="위에서 '직접 입력'을 골랐을 때만 채워주세요 (MM/DD)",
            )
        )
        self.add_item(
            discord.ui.Label(
                "시간",
                discord.ui.InputText(
                    placeholder="오후 3시 30분",
                    value=raw.get("시간"),
                    max_length=20,
                ),
                description="오후 3시 · 3시 30분 · 15:30 · 1530 · 저녁 8시 다 됩니다",
            )
        )
        self.add_item(
            discord.ui.Label(
                "내용",
                discord.ui.InputText(
                    style=discord.InputTextStyle.paragraph,
                    placeholder="정기 회의 - 다음 영상 기획 회의",
                    value=raw.get("내용"),
                    max_length=500,
                ),
            )
        )
        self.add_item(
            discord.ui.Label(
                "대상",
                discord.ui.MentionableSelect(
                    placeholder="멘션할 멤버/역할을 선택하세요 (없으면 비워두세요)",
                    min_values=0,
                    max_values=MAX_PICK,
                    required=False,
                    default_values=session.picked or [],
                ),
                description="멤버와 역할을 여러 개 선택 가능 · 없으면 비워두세요",
            )
        )

    async def callback(self, interaction: discord.Interaction):
        session = self.session
        session.date_choice = _first_value(self, 0)
        session.raw["날짜 직접 입력"] = _text(self, 1)
        session.raw["시간"] = _text(self, 2)
        session.raw["내용"] = _text(self, 3)
        session.picked = list(_item(self, 4).values or [])

        content = session.raw["내용"].strip()
        if not content:
            return await self._retry(interaction, "일정 내용을 입력해주세요.")

        try:
            target_time = self._resolve_time(session)
        except ValueError as exc:
            return await self._retry(interaction, str(exc))

        await self.cog.register_schedule(interaction, target_time, content, session.picked)

    def _resolve_time(self, session: ScheduleSession) -> datetime:
        """선택한 날짜(또는 직접 입력) + 시간을 KST `datetime` 으로 만든다."""
        now = datetime.now(KST)
        choice = session.date_choice
        custom = session.raw.get("날짜 직접 입력", "").strip()

        if choice == CUSTOM_DATE or (not choice and custom):
            if not custom:
                raise ValueError(
                    "'직접 입력'을 고르셨으니 '날짜 직접 입력' 칸에 `MM/DD` 를 적어주세요."
                )
            day = parse_date(custom, now)
        elif choice:
            day = date.fromisoformat(choice)
        else:
            raise ValueError("날짜를 선택해주세요.")

        hour, minute = parse_time(session.raw.get("시간", ""))
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)

    async def _retry(self, interaction: discord.Interaction, reason: str):
        await interaction.response.send_message(
            f"❌ {reason}\n아래 버튼으로 다시 입력해주세요. (입력했던 값은 그대로 채워집니다)",
            view=ScheduleRetryView(self.cog, self.session),
            ephemeral=True,
        )


class ScheduleRetryView(discord.ui.View):
    """모달 → 모달 제약을 우회하기 위한 재입력 버튼."""

    def __init__(self, cog: "EventListener", session: ScheduleSession):
        super().__init__(timeout=VIEW_TIMEOUT, disable_on_timeout=True)
        self.cog = cog
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.session.user_id:
            return True
        await interaction.response.send_message(
            "❌ 명령어를 실행한 사람만 사용할 수 있습니다.", ephemeral=True
        )
        return False

    @discord.ui.button(label="다시 입력", style=discord.ButtonStyle.primary, emoji="📝")
    async def open_modal(self, button: discord.ui.Button, interaction: discord.Interaction):
        # 버튼 클릭 interaction 이므로 모달을 열 수 있다.
        self.disable_all_items()
        try:
            await interaction.response.send_modal(ScheduleModal(self.cog, self.session))
        except discord.HTTPException as exc:
            logger.error("일정 모달 열기 실패: %s", exc)
            return
        self.stop()


class CancelSelect(discord.ui.Select):
    """취소할 일정을 고르는 드롭다운. 번호를 외울 필요가 없다."""

    def __init__(self, cog: "EventListener", schedules: list[dict]):
        options = [
            discord.SelectOption(
                label=f"{m['time'].strftime('%m/%d %H:%M')} · {m['content']}"[:100],
                value=str(m["id"]),
                description=(f"등록자 {m.get('author', '?')}")[:100],
            )
            for m in schedules[:MAX_OPTIONS]
        ]
        super().__init__(
            placeholder="취소할 일정을 선택하세요",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        removed = self.cog.remove_schedule(self.values[0])
        self.disabled = True
        if removed is None:
            return await interaction.response.edit_message(
                content="⚠️ 이미 지나갔거나 취소된 일정입니다.", view=None
            )
        await interaction.response.edit_message(
            content=(
                f"🗑️ 취소됨: **{removed['content']}**\n"
                f"🕒 {_ts(removed['time'])}"
            ),
            view=None,
        )


class CancelView(discord.ui.View):
    def __init__(self, cog: "EventListener", user_id: int, schedules: list[dict]):
        super().__init__(timeout=VIEW_TIMEOUT, disable_on_timeout=True)
        self.user_id = user_id
        self.add_item(CancelSelect(cog, schedules))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "❌ 명령어를 실행한 사람만 사용할 수 있습니다.", ephemeral=True
        )
        return False


class EventListener(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.target_channel_id = 1472943305644965888
        self.allowed_roles = ["!"]
        # 절대 경로를 사용해서 어떤 경로에서 봇을 켜도 데이터가 유지되도록 함
        self.db_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schedules.json")
        self.db_file = os.path.normpath(self.db_file)
        self.meeting_schedule = self.load_schedules()
        self.check_schedule.start()

    def cog_unload(self):
        self.check_schedule.cancel()

    def load_schedules(self):
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        dt = datetime.fromisoformat(item["time"])
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=KST)
                        item["time"] = dt
                    return data
            except Exception as e:
                print(f"데이터 로드 에러: {e}")
                return []
        return []

    def save_schedules(self):
        try:
            data_to_save = []
            for item in self.meeting_schedule:
                copy_item = item.copy()
                copy_item["time"] = item["time"].isoformat()
                data_to_save.append(copy_item)
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"데이터 저장 에러: {e}")

    async def is_admin(self, ctx):
        if not hasattr(ctx.author, "roles"): return False
        user_role_names = [role.name for role in ctx.author.roles]
        return any(role in user_role_names for role in self.allowed_roles)

    async def get_target_channel(self):
        channel = self.bot.get_channel(self.target_channel_id)
        if not channel:
            try: channel = await self.bot.fetch_channel(self.target_channel_id)
            except: return None
        return channel

    # 일정 등록은 누구나 할 수 있다. (취소는 아래처럼 본인 또는 관리진으로 제한한다 —
    # 남의 일정을 지울 수 있기 때문)
    @discord.slash_command(name="일정", description="새 일정을 예약합니다.")
    async def set_meeting(self, ctx: discord.ApplicationContext):
        await ctx.send_modal(ScheduleModal(self, ScheduleSession(ctx.author.id)))

    async def register_schedule(
        self,
        interaction: discord.Interaction,
        target_time: datetime,
        content: str,
        picked: list,
    ) -> None:
        """모달 입력을 일정으로 저장하고 알림 채널에 공지한다."""
        mentions = _mentions_of(picked)

        meeting_info = {
            "id": interaction.id,
            "author": getattr(interaction.user, "display_name", str(interaction.user)),
            # 본인이 등록한 일정을 스스로 취소할 수 있게 ID 도 함께 남긴다.
            "author_id": getattr(interaction.user, "id", None),
            "time": target_time,
            "content": content,
            "mention": mentions,
            "notified_10m": False,
        }

        self.meeting_schedule.append(meeting_info)
        self.meeting_schedule.sort(key=lambda x: x["time"])
        self.save_schedules()

        logger.info(
            "일정 등록: %s / %s / 등록자=%s(%s)",
            target_time.strftime("%Y-%m-%d %H:%M"),
            content,
            interaction.user,
            getattr(interaction.user, "id", "?"),
        )

        past_note = (
            "\n⚠️ 이미 지난 시각입니다. 잘못 입력했다면 `/일정취소` 로 지워주세요."
            if target_time <= datetime.now(KST)
            else ""
        )
        await interaction.response.send_message(
            f"✅ 일정 등록 완료: **{content}**\n"
            f"🕒 {_ts(target_time)} ({_ts(target_time, 'R')})\n"
            f"👤 {mentions or '대상 없음'}"
            f"{past_note}"
        )

        channel = await self.get_target_channel()
        if channel:
            embed = discord.Embed(title="📅 새 일정 알림", description=f"**{content}**", color=0x5865F2)
            embed.add_field(
                name="시간", value=f"{_ts(target_time)}\n{_ts(target_time, 'R')}", inline=False
            )
            embed.set_footer(text=f"등록자 {meeting_info['author']}")
            await channel.send(content=f"{mentions} 확인해주세요!" if mentions else None, embed=embed)

    def remove_schedule(self, schedule_id: str) -> dict | None:
        """id 로 일정을 지운다. 이미 없으면 None."""
        for index, m in enumerate(self.meeting_schedule):
            if str(m.get("id")) == str(schedule_id):
                removed = self.meeting_schedule.pop(index)
                self.save_schedules()
                logger.info(
                    "일정 취소: %s / %s",
                    removed["time"].strftime("%Y-%m-%d %H:%M"),
                    removed["content"],
                )
                return removed
        return None

    @discord.slash_command(name="일정목록", description="예약된 일정을 확인합니다.")
    async def list_meetings(self, ctx: discord.ApplicationContext):
        if not self.meeting_schedule:
            return await ctx.respond("📅 예약된 일정이 없습니다.")

        embed = discord.Embed(title="📋 일정 목록", color=0x3498db)
        for i, m in enumerate(self.meeting_schedule[:10], 1):
            embed.add_field(
                name=f"{i}. {m['content']}",
                value=(
                    f"🕒 {_ts(m['time'])} ({_ts(m['time'], 'R')})\n"
                    f"👤 {m['mention'] or '대상 없음'} · 등록자 {m.get('author', '?')}"
                ),
                inline=False,
            )
        if len(self.meeting_schedule) > 10:
            embed.set_footer(text=f"외 {len(self.meeting_schedule) - 10}건 더 있습니다.")
        await ctx.respond(embed=embed)

    @discord.slash_command(name="일정취소", description="예약한 일정을 목록에서 골라 취소합니다.")
    async def cancel_meeting(self, ctx: discord.ApplicationContext):
        if not self.meeting_schedule:
            return await ctx.respond("📅 예약된 일정이 없습니다.", ephemeral=True)

        if await self.is_admin(ctx):
            targets = list(self.meeting_schedule)
        else:
            # 관리진이 아니면 자기가 등록한 일정만 지울 수 있다.
            targets = [
                m for m in self.meeting_schedule if m.get("author_id") == ctx.author.id
            ]
            if not targets:
                return await ctx.respond(
                    "❌ 취소할 수 있는 일정이 없습니다. (직접 등록한 일정만 취소할 수 있어요)",
                    ephemeral=True,
                )

        hidden = len(targets) - MAX_OPTIONS
        content = "취소할 일정을 선택하세요."
        if hidden > 0:
            content += f" (가까운 {MAX_OPTIONS}건만 표시 · 외 {hidden}건)"
        await ctx.respond(
            content, view=CancelView(self, ctx.author.id, targets), ephemeral=True
        )

    @tasks.loop(seconds=20)
    async def check_schedule(self):
        now = datetime.now(KST)
        channel = await self.get_target_channel()
        if not channel: return

        changed = False
        new_schedule = []

        for m in self.meeting_schedule:
            time_diff = m["time"] - now

            if timedelta(minutes=0) < time_diff <= timedelta(minutes=10) and not m.get("notified_10m"):
                embed = discord.Embed(title="⚠️ 일정 10분 전!", description=f"**{m['content']}**", color=0xFFA500)
                embed.add_field(name="시간", value=_ts(m["time"], "t"), inline=True)
                await channel.send(content=m["mention"], embed=embed)
                m["notified_10m"] = True
                changed = True

            if now >= m["time"]:
                embed = discord.Embed(title="🚀 일정 시작!", description=f"**{m['content']}**", color=0xFF0000)
                await channel.send(content=m["mention"], embed=embed)
                changed = True
                continue

            new_schedule.append(m)

        if changed:
            self.meeting_schedule = new_schedule
            self.save_schedules()

    @check_schedule.before_loop
    async def before_check_schedule(self):
        await self.bot.wait_until_ready()

def setup(bot):
    bot.add_cog(EventListener(bot))
