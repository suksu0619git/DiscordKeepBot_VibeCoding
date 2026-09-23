"""FR-1 영상 크레딧 생성 슬래시 명령어.

입력 UI 는 요구사항의 **옵션 A** 구현이다.

    /크레딧 → 1차 모달(5칸) → [제출]
           → ephemeral 안내 + "2단계 입력" 버튼(View)
           → 버튼 클릭 → 2차 모달(4칸) → [제출]
           → 크레딧 코드블록 출력 + FR-2 자동 갱신 결과 ephemeral 안내

참여자 5개 역할(Production/Edit/3D/Filming/Act)은 텍스트가 아니라 **유저 선택
메뉴**로 받는다. `discord.ui.DesignerModal` + `Label` 조합이라야 모달 안에 선택
메뉴를 넣을 수 있다(구형 `discord.ui.Modal` 은 텍스트 입력만 지원). 선택 결과로
user_id 를 얻으므로 FR-2 활동 갱신이 닉네임 문자열 대신 ID 로 매칭된다.

Discord API 는 **모달 제출 interaction 에 대한 응답으로 다시 모달을 열 수 없다.**
그래서 1차 제출에는 버튼이 달린 메시지로 응답하고, 그 버튼 클릭 interaction 에서
2차 모달을 연다. 단계 간 입력값은 `CreditSession` 인스턴스가 보관한다.
모달 컴포넌트는 5개가 상한이라 1단계 = 제목 + 역할 4개로 정확히 맞춰져 있다.
"""

from __future__ import annotations

import io
import logging

import discord
from discord.ext import commands

from services.credit_format import (
    OPTIONAL_ROLES,
    STEP1_LABELS,
    CreditData,
    CreditPerson,
    format_credit,
    parse_tags,
    wrap_codeblock,
)

logger = logging.getLogger(__name__)

VIEW_TIMEOUT = 600  # 10분
MAX_PICK = 25  # 선택 메뉴 1개당 고를 수 있는 최대 인원 (디스코드 제한)


class CreditSession:
    """단계별 모달 입력값을 모아두는 컨테이너."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.data = CreditData()
        # 재입력 시 모달을 채워두기 위해 원문 입력을 그대로 보관한다.
        self.raw: dict[str, str] = {}
        # 역할 → 선택된 Member/User. 모달을 다시 열 때 기본 선택으로 되돌린다.
        self.picked: dict[str, list] = {}

    def absorb_step1(self, title: str, picks: dict[str, list]) -> None:
        self.raw["제목"] = title
        self.picked.update(picks)
        self.data.title = title
        self.data.production = _to_people(picks["Production"])
        self.data.edit = _to_people(picks["Edit"])
        self.data.three_d = _to_people(picks["3D"])
        self.data.filming = _to_people(picks["Filming"])

    def absorb_step2(self, values: dict[str, str], picks: dict[str, list]) -> None:
        self.raw.update(values)
        self.picked.update(picks)
        self.data.act = _to_people(picks["Act"])
        self.data.music_link = values["음악 링크"]
        self.data.world = values["World"]
        self.data.tags = parse_tags(values["태그"])


def _to_people(members: list) -> list[CreditPerson]:
    """선택된 Member/User 목록을 `CreditPerson` 으로 변환한다(순서 유지, 중복 제거)."""
    people: list[CreditPerson] = []
    seen: set[int] = set()
    for member in members or []:
        user_id = getattr(member, "id", None)
        if user_id is None or user_id in seen:
            continue
        seen.add(user_id)
        # display_name 은 서버 별명 → 표시 이름 → 계정명 순으로 채워진다.
        name = getattr(member, "display_name", None) or getattr(member, "name", "")
        people.append(CreditPerson(name=str(name), user_id=user_id))
    return people


def _item(modal: discord.ui.DesignerModal, index: int):
    """DesignerModal 의 자식은 Label 이므로 안에 든 실제 입력 컴포넌트를 꺼낸다."""
    return modal.children[index].item


def _text(modal: discord.ui.DesignerModal, index: int) -> str:
    return _item(modal, index).value or ""


def _picked(modal: discord.ui.DesignerModal, index: int) -> list:
    """유저 선택 메뉴에서 고른 Member/User 목록."""
    return list(_item(modal, index).values or [])


def _user_select(session: CreditSession, role: str) -> discord.ui.UserSelect:
    """역할별 멤버 선택 메뉴. 이전에 고른 값이 있으면 기본 선택으로 되살린다.

    3D 처럼 영상에 따라 참여자가 없는 역할(`OPTIONAL_ROLES`)은 `min_values=0`
    + `required=False` 로 두어 아무도 고르지 않아도 모달이 제출된다.
    (`required=True` 와 `min_values=0` 을 함께 주면 py-cord 가 ValueError 를 낸다)
    """
    optional = role in OPTIONAL_ROLES
    return discord.ui.UserSelect(
        placeholder=(
            f"{role} 참여자를 선택하세요 (없으면 비워두세요)"
            if optional
            else f"{role} 참여자를 선택하세요"
        ),
        min_values=0 if optional else 1,
        max_values=MAX_PICK,
        required=not optional,
        default_values=session.picked.get(role) or [],
    )


def _role_hint(role: str) -> str:
    """선택 메뉴 아래 설명. 비워둘 수 있는 역할은 그 사실을 명시한다."""
    if role in OPTIONAL_ROLES:
        return "여러 명 선택 가능 · 없으면 비워두세요 (크레딧에서 줄이 빠집니다)"
    return "여러 명 선택 가능"


class CreditStep1Modal(discord.ui.DesignerModal):
    """1단계: 제목 / Production / Edit / 3D / Filming

    역할 4칸은 유저 선택 메뉴다. 텍스트가 아니라 실제 멤버를 고르므로 user_id 가
    함께 넘어오고, FR-2 활동 갱신이 닉네임 문자열에 의존하지 않게 된다.
    """

    def __init__(self, cog: "CreditCog", session: CreditSession):
        super().__init__(title="영상 크레딧 (1/2)", timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.session = session
        self.add_item(
            discord.ui.Label(
                "제목",
                discord.ui.InputText(
                    placeholder="VRC Unity Noob vs Pro",
                    value=session.raw.get("제목"),
                    max_length=300,
                ),
            )
        )
        for role in ("Production", "Edit", "3D", "Filming"):
            self.add_item(
                discord.ui.Label(
                    role,
                    _user_select(session, role),
                    description=_role_hint(role),
                )
            )

    async def callback(self, interaction: discord.Interaction):
        try:
            self.session.absorb_step1(
                title=_text(self, 0),
                picks={
                    "Production": _picked(self, 1),
                    "Edit": _picked(self, 2),
                    "3D": _picked(self, 3),
                    "Filming": _picked(self, 4),
                },
            )
        except Exception:
            logger.exception("크레딧 1단계 입력 처리 실패")
            return await interaction.response.send_message(
                "❌ 입력 처리 중 오류가 발생했습니다. 다시 시도해주세요.", ephemeral=True
            )

        missing = self.session.data.missing_labels(STEP1_LABELS)
        if missing:
            # 모달 제출 응답으로 다시 모달을 열 수 없으므로, 재입력 버튼을 제공한다.
            return await interaction.response.send_message(
                f"❌ 다음 항목이 비어 있습니다: **{', '.join(missing)}**\n"
                "아래 버튼으로 1단계를 다시 입력해주세요. (입력했던 값은 그대로 채워집니다)",
                view=CreditStepView(self.cog, self.session, step=1),
                ephemeral=True,
            )

        await interaction.response.send_message(
            "✅ 1단계 입력 완료. 아래 버튼을 눌러 2단계(Act / 음악 링크 / World / 태그)를 입력해주세요.",
            view=CreditStepView(self.cog, self.session, step=2),
            ephemeral=True,
        )


class CreditStep2Modal(discord.ui.DesignerModal):
    """2단계: Act / 음악 링크 / World / 태그"""

    def __init__(self, cog: "CreditCog", session: CreditSession):
        super().__init__(title="영상 크레딧 (2/2)", timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.session = session
        raw = session.raw
        self.add_item(
            discord.ui.Label(
                "Act", _user_select(session, "Act"), description=_role_hint("Act")
            )
        )
        self.add_item(
            discord.ui.Label(
                "음악 링크",
                discord.ui.InputText(
                    placeholder="https://youtu.be/zql8G-4gE7w",
                    value=raw.get("음악 링크"),
                    max_length=500,
                    required=False,
                ),
                description="없으면 비워두세요 (MUSIC 단락이 빠집니다)",
            )
        )
        self.add_item(
            discord.ui.Label(
                "World",
                discord.ui.InputText(
                    placeholder="Studio KEEP - Studio KEEP KEXCO",
                    value=raw.get("World"),
                    max_length=300,
                ),
            )
        )
        self.add_item(
            discord.ui.Label(
                "태그",
                discord.ui.InputText(
                    style=discord.InputTextStyle.paragraph,
                    placeholder="vrchat, 쇼팽, 유니티, Unity, noob, pro, 꿀팁, 추천, kipfel, shorts",
                    value=raw.get("태그"),
                    max_length=1000,
                ),
                description="쉼표 또는 공백으로 구분",
            )
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            self.session.absorb_step2(
                values={
                    "음악 링크": _text(self, 1),
                    "World": _text(self, 2),
                    "태그": _text(self, 3),
                },
                picks={"Act": _picked(self, 0)},
            )
        except Exception:
            logger.exception("크레딧 2단계 입력 처리 실패")
            return await interaction.response.send_message(
                "❌ 입력 처리 중 오류가 발생했습니다. 다시 시도해주세요.", ephemeral=True
            )

        missing = self.session.data.missing_labels()
        if missing:
            step = 1 if any(label in STEP1_LABELS for label in missing) else 2
            return await interaction.response.send_message(
                f"❌ 다음 항목이 비어 있습니다: **{', '.join(missing)}**\n"
                f"아래 버튼으로 {step}단계를 다시 입력해주세요.",
                view=CreditStepView(self.cog, self.session, step=step),
                ephemeral=True,
            )

        await self.cog.publish_credit(interaction, self.session)


class CreditStepView(discord.ui.View):
    """모달 → 모달 제약을 우회하기 위한 단계 이동 버튼."""

    def __init__(self, cog: "CreditCog", session: CreditSession, step: int):
        super().__init__(timeout=VIEW_TIMEOUT, disable_on_timeout=True)
        self.cog = cog
        self.session = session
        self.step = step
        button = self.children[0]
        button.label = "1단계 다시 입력" if step == 1 else "2단계 입력"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.session.user_id:
            return True
        await interaction.response.send_message(
            "❌ 명령어를 실행한 사람만 사용할 수 있습니다.", ephemeral=True
        )
        return False

    @discord.ui.button(label="다음 단계 입력", style=discord.ButtonStyle.primary, emoji="📝")
    async def open_modal(self, button: discord.ui.Button, interaction: discord.Interaction):
        # 버튼 클릭 interaction 이므로 모달을 열 수 있다.
        modal = (
            CreditStep1Modal(self.cog, self.session)
            if self.step == 1
            else CreditStep2Modal(self.cog, self.session)
        )
        self.disable_all_items()
        try:
            await interaction.response.send_modal(modal)
        except discord.HTTPException as exc:
            logger.error("크레딧 모달 열기 실패: %s", exc)
            return
        self.stop()


class CreditCog(commands.Cog):
    """영상 크레딧 생성."""

    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="크레딧", description="영상 크레딧을 입력받아 지정 포맷으로 출력합니다.")
    async def credit(self, ctx: discord.ApplicationContext):
        session = CreditSession(ctx.author.id)
        await ctx.send_modal(CreditStep1Modal(self, session))

    # ------------------------------------------------------------------ #
    # 출력 + FR-2 연동
    # ------------------------------------------------------------------ #
    async def publish_credit(
        self, interaction: discord.Interaction, session: CreditSession
    ) -> None:
        """크레딧 코드블록을 출력하고, 활동 관리 Cog에 자동 갱신을 요청한다."""
        data = session.data
        body = format_credit(data)
        message = wrap_codeblock(body)

        if len(message) > 2000:
            logger.warning("크레딧 본문이 2000자를 초과했습니다 (%d자). 파일로 전송합니다.", len(message))
            await interaction.response.send_message(
                "⚠️ 내용이 길어 코드블록 대신 파일로 전송합니다.",
                file=discord.File(
                    fp=io.BytesIO(body.encode("utf-8")), filename="credit.txt"
                ),
            )
        else:
            await interaction.response.send_message(message)

        logger.info(
            "크레딧 생성: 제목=%r 제출자=%s(%s)",
            data.title.strip(),
            interaction.user,
            getattr(interaction.user, "id", "?"),
        )
        await self._sync_activity(interaction, data)

    async def _sync_activity(
        self, interaction: discord.Interaction, data: CreditData
    ) -> None:
        """FR-2.5 자동 갱신. 실패해도 크레딧 출력에는 영향을 주지 않는다."""
        tracker = self.bot.get_cog("ActivityTracker")
        if tracker is None:
            logger.warning("ActivityTracker Cog 를 찾을 수 없어 활동 자동 갱신을 건너뜁니다.")
            return

        try:
            result = await tracker.apply_credit_activity(
                data.people_by_role(), data.title.strip()
            )
        except Exception:
            logger.exception("활동 자동 갱신 호출 실패")
            try:
                await interaction.followup.send(
                    "⚠️ 크레딧은 출력되었지만 활동 기간 자동 갱신에 실패했습니다. 로그를 확인해주세요.",
                    ephemeral=True,
                )
            except discord.HTTPException as exc:
                logger.warning("자동 갱신 실패 안내 전송 실패: %s", exc)
            return

        def mentions(people) -> str:
            # 유저 선택으로 받았으면 user_id 가 있다. 멘션이면 누구인지 명확하다.
            return ", ".join(
                f"<@{person.user_id}>" if person.user_id else f"`{person.name}`"
                for person in people
            )

        lines = []
        if result.registered_people:
            lines.append(
                f"🆕 활동 자동 등록 ({len(result.registered_people)}명): "
                + mentions(result.registered_people)
            )
        # 자동 등록된 인원도 갱신 목록에 함께 잡히므로, 신규분은 위에서만 보여준다.
        renewed = [
            name
            for name in result.updated_nicknames
            if name not in result.registered_nicknames
        ]
        if renewed:
            lines.append(
                f"✅ 활동 기간 갱신 ({len(renewed)}명): "
                + ", ".join(f"`{name}`" for name in renewed)
            )
        if result.conflict_people:
            lines.append(
                f"⚠️ 자동 등록 실패 (닉네임 중복): {mentions(result.conflict_people)}\n"
                "→ 이미 같은 닉네임을 쓰는 다른 멤버가 있습니다. `/활동등록` 으로 직접 등록해주세요."
            )
        if result.unknown_people:
            lines.append(
                f"⚠️ 활동 미등록: {mentions(result.unknown_people)}\n"
                "→ `/활동등록` 으로 등록하면 다음 크레딧부터 자동 갱신됩니다."
            )
        if not lines:
            return

        try:
            await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)
        except discord.HTTPException as exc:
            logger.warning("활동 갱신 결과 안내 전송 실패: %s", exc)


def setup(bot: discord.Bot):
    bot.add_cog(CreditCog(bot))
