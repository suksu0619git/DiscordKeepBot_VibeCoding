"""FR-1 영상 크레딧 텍스트 포맷터.

Discord 의존성이 전혀 없는 순수 로직만 둔다(단위 테스트로 포맷 100% 일치를 검증).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 이름 목록 구분자: 쉼표(전각 포함)와 줄바꿈만 사용한다.
# "깜 냐 옹" 처럼 이름 안에 공백이 들어갈 수 있으므로 공백으로 나누면 안 된다.
_NAME_SEPARATORS = re.compile(r"[,，\n\r]+")
# 태그는 공백을 포함할 수 없으므로 공백/쉼표/# 모두 구분자로 취급한다.
_TAG_SEPARATORS = re.compile(r"[\s,，#]+")

# 크레딧 역할 라벨 → FR-2 credit_logs.role_in_video 값
ROLE_LABELS = ("Production", "Edit", "3D", "Filming", "Act")

# 영상에 따라 참여자·내용이 아예 없을 수 있는 항목. 입력에서 비워둘 수 있고,
# 비어 있으면 `format_credit` 이 해당 줄을 출력하지 않는다.
OPTIONAL_ROLES = ("3D", "Filming", "Act")
OPTIONAL_LABELS = OPTIONAL_ROLES + ("음악 링크",)

# Discord Modal 은 컴포넌트 5개 제한이 있어 입력을 2단계로 나눈다.
STEP1_LABELS = ("제목", "Production", "Edit", "3D", "Filming")
STEP2_LABELS = ("Act", "음악 링크", "World", "태그")


@dataclass(frozen=True)
class CreditPerson:
    """크레딧에 이름이 올라가는 사람.

    `user_id` 는 모달의 유저 선택 메뉴로 고른 경우에만 채워진다. 값이 있으면 FR-2
    활동 갱신이 닉네임 문자열 대신 ID 로 매칭하므로 개명/오타에 깨지지 않는다.
    """

    name: str
    user_id: int | None = None

    def __str__(self) -> str:  # format_credit 의 join 에서 그대로 쓰인다.
        return self.name


def people_from_names(names: list[str] | str) -> list[CreditPerson]:
    """이름 문자열(또는 목록)을 `CreditPerson` 목록으로 만든다(user_id 없음).

    선택 UI 를 쓸 수 없는 경로(테스트, 과거 데이터)를 위한 보조 생성자다.

    >>> people_from_names("AT_Cat, RUCOO")
    [CreditPerson(name='AT_Cat', user_id=None), CreditPerson(name='RUCOO', user_id=None)]
    """
    if isinstance(names, str):
        names = parse_names(names)
    return [CreditPerson(name=name) for name in names]


def parse_names(raw: str) -> list[str]:
    """쉼표/줄바꿈으로 구분된 이름 목록을 파싱한다(순서 유지, 중복 제거).

    >>> parse_names("AT_Cat, RUCOO")
    ['AT_Cat', 'RUCOO']
    >>> parse_names("잉어에요, 깜 냐 옹")
    ['잉어에요', '깜 냐 옹']
    """
    result: list[str] = []
    for chunk in _NAME_SEPARATORS.split(raw or ""):
        name = chunk.strip()
        if name and name not in result:
            result.append(name)
    return result


def parse_tags(raw: str) -> list[str]:
    """태그 목록을 파싱한다. 입력의 '#' 유무와 구분자(쉼표/공백)를 모두 허용한다.

    >>> parse_tags("vrchat, 쇼팽 #유니티")
    ['vrchat', '쇼팽', '유니티']
    """
    result: list[str] = []
    for chunk in _TAG_SEPARATORS.split(raw or ""):
        tag = chunk.strip()
        if tag and tag not in result:
            result.append(tag)
    return result


def _strip_wrapper(value: str, opening: str, closing: str) -> str:
    """이미 감싸져 입력된 경우 겹쳐 감싸지 않도록 바깥 기호를 벗긴다."""
    value = (value or "").strip()
    while value.startswith(opening) and value.endswith(closing) and len(value) >= 2:
        value = value[1:-1].strip()
    return value


@dataclass
class CreditData:
    """크레딧 입력값 묶음. 음악은 링크 한 칸만 받는다."""

    title: str = ""
    production: list[CreditPerson] = field(default_factory=list)
    edit: list[CreditPerson] = field(default_factory=list)
    three_d: list[CreditPerson] = field(default_factory=list)
    filming: list[CreditPerson] = field(default_factory=list)
    act: list[CreditPerson] = field(default_factory=list)
    music_link: str = ""
    world: str = ""
    tags: list[str] = field(default_factory=list)

    # 검증 및 안내 메시지에 쓰는 라벨 (출력 라벨과 동일하게 유지).
    # `OPTIONAL_LABELS` 항목은 비워둘 수 있으므로 여기에 넣지 않는다.
    _REQUIRED_LABELS = (
        ("제목", "title"),
        ("Production", "production"),
        ("Edit", "edit"),
        ("World", "world"),
        ("태그", "tags"),
    )

    def missing_labels(self, subset: tuple[str, ...] | None = None) -> list[str]:
        """비어 있는 필수 항목의 라벨 목록(공백만 입력한 경우도 누락으로 본다).

        `subset` 을 주면 해당 라벨만 검사한다(단계별 모달 검증용).
        """
        missing = []
        for label, attribute in self._REQUIRED_LABELS:
            if subset is not None and label not in subset:
                continue
            value = getattr(self, attribute)
            if isinstance(value, str):
                if not value.strip():
                    missing.append(label)
            elif not value:
                missing.append(label)
        return missing

    def people_by_role(self) -> dict[str, list[CreditPerson]]:
        """FR-2.5 자동 갱신에 넘길 {역할: [사람]} 매핑."""
        return {
            "Production": list(self.production),
            "Edit": list(self.edit),
            "3D": list(self.three_d),
            "Filming": list(self.filming),
            "Act": list(self.act),
        }

    def names_by_role(self) -> dict[str, list[str]]:
        """{역할: [표시 이름]} 매핑(출력·안내용)."""
        return {
            role: [person.name for person in people]
            for role, people in self.people_by_role().items()
        }

    def all_names(self) -> list[str]:
        seen: list[str] = []
        for names in self.names_by_role().values():
            for name in names:
                if name not in seen:
                    seen.append(name)
        return seen


def format_credit(data: CreditData) -> str:
    """크레딧 본문을 생성한다. 줄바꿈/공백까지 지정 포맷과 동일해야 한다.

    3D 처럼 영상에 따라 참여자가 없을 수 있는 항목은 **줄 자체를 출력하지 않는다**.
    빈 `3D : ` 줄을 남기면 영상 설명란에 그대로 붙여넣을 때 지저분하기 때문이다.
    """
    def join(people: list[CreditPerson]) -> str:
        return ", ".join(person.name for person in people)

    lines = [f"제목 : {data.title.strip()}"]
    for label, people in (
        ("Production", data.production),
        ("Edit", data.edit),
        ("3D", data.three_d),
        ("Filming", data.filming),
        ("Act", data.act),
    ):
        if people:
            lines.append(f"{label} : {join(people)}")

    music_link = _strip_wrapper(data.music_link, "(", ")")
    if music_link:
        # 링크가 비어 있으면 "MUSIC" 머리말과 앞 빈 줄까지 통째로 뺀다.
        lines += ["", "MUSIC", f"({music_link})"]

    lines += [
        "",
        "World",
        _strip_wrapper(data.world, "{", "}"),
        "",
        # 태그는 "#" 없이 쉼표로 나열한다(영상 설명란에 그대로 붙여넣는 형식).
        "태그 : " + ", ".join(data.tags),
    ]
    return "\n".join(lines)


def wrap_codeblock(text: str) -> str:
    """디스코드 복사 버튼이 붙도록 ```text 코드블록으로 감싼다."""
    # 본문에 백틱 3개가 들어가면 코드블록이 깨지므로 방어적으로 치환한다.
    safe = text.replace("```", "``​`")
    return f"```text\n{safe}\n```"
