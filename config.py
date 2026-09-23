"""KeepBot 전역 설정.

역할 ID / 채널 ID / 개발자 ID / DB 경로 등 **모든** 설정값은 이 모듈을 통해서만
읽는다. Cog 안에 ID를 하드코딩하지 말고 여기에 키를 추가한다. 각 키의 기본값도
이 파일이 유일한 근거이므로, `.env` 에 없는 키는 여기 적힌 기본값으로 동작한다.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# .env 탐색 순서(먼저 찾은 하나만 읽는다):
#   ① 환경변수 KEEPBOT_ENV_FILE 로 명시한 경로 (일회성 실행용)
#   ② 봇 폴더/.env
#   ③ 상위 폴더/.env    ← EC2 에서 저장소 바깥(~/KeepBot/.env)에 둘 때
# 암묵적인 상향 탐색에 의존하지 않고 경로를 명시해 EC2/로컬 동작을 일치시킨다.
# 기동 로그에 실제로 읽은 경로가 찍히므로 어느 파일이 쓰였는지 항상 확인할 수 있다.
DOTENV_PATH: str | None = None
for _candidate in (
    os.getenv("KEEPBOT_ENV_FILE"),
    os.path.join(BASE_DIR, ".env"),
    os.path.join(os.path.dirname(BASE_DIR), ".env"),
):
    if not _candidate:
        continue
    if os.path.isfile(_candidate):
        load_dotenv(_candidate)
        DOTENV_PATH = _candidate
        break
else:  # 파일을 못 찾으면 기본 동작(상향 탐색)에 맡긴다.
    load_dotenv()


def _get_str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    return value.strip() if value and value.strip() else default


def _get_int(key: str, default: int | None = None) -> int | None:
    raw = _get_str(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("설정 %s 값이 정수가 아닙니다: %r (기본값 %r 사용)", key, raw, default)
        return default


def _get_int_req(key: str, default: int) -> int:
    """`_get_int` 와 같지만 항상 int 를 돌려준다(0 을 유효한 값으로 쓰는 키용)."""
    value = _get_int(key, default)
    return default if value is None else value


def _get_bool(key: str, default: bool) -> bool:
    raw = _get_str(key).lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    logger.warning("설정 %s 값이 참/거짓이 아닙니다: %r (기본값 %r 사용)", key, raw, default)
    return default


def _get_int_list(key: str) -> list[int]:
    raw = _get_str(key)
    if not raw:
        return []
    result: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.append(int(chunk))
        except ValueError:
            logger.warning("설정 %s 에 정수가 아닌 항목이 있습니다: %r (무시)", key, chunk)
    return result


# ---------------------------------------------------------------- 봇 코어
TOKEN: str = _get_str("TOKEN")
LOG_LEVEL: str = _get_str("LOG_LEVEL", "INFO").upper()

# 슬래시 명령어 자동 동기화. py-cord 는 접속 시 "코드에 없는 등록 명령어"를 지우므로,
# 등록을 건드리고 싶지 않을 때 끌 수 있게 해 둔다.
AUTO_SYNC_COMMANDS: bool = _get_bool("AUTO_SYNC_COMMANDS", True)

# ---------------------------------------------------------------- FR-4 개발자 전용
DEV_USER_IDS: frozenset[int] = frozenset(_get_int_list("DEV_USER_IDS"))

# ---------------------------------------------------------------- FR-3 유튜브 → 포럼
YOUTUBE_API_KEY: str = _get_str("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID: str = _get_str("YOUTUBE_CHANNEL_ID")
YOUTUBE_CHECK_INTERVAL_MINUTES: int = _get_int("YOUTUBE_CHECK_INTERVAL_MINUTES", 5) or 5
FORUM_CHANNEL_ID: int | None = _get_int("FORUM_CHANNEL_ID")
# 태그는 이름 또는 태그 ID 어느 쪽으로 적어도 동작한다(숫자면 ID로 간주).
YOUTUBE_LONGFORM_TAG_NAME: str = _get_str("YOUTUBE_LONGFORM_TAG_NAME", "롱폼")
YOUTUBE_SHORTS_TAG_NAME: str = _get_str("YOUTUBE_SHORTS_TAG_NAME", "쇼츠")
YOUTUBE_STATE_PATH: str = _get_str(
    "YOUTUBE_STATE_PATH", os.path.join(BASE_DIR, "youtube_state.json")
)

# ---------------------------------------------------------------- FR-5 익명 채팅
# 여러 채널을 익명으로 굴릴 수 있다. 신규 설정은 복수형 키를 쓴다.
# 단수형 키는 예전 .env 와의 호환을 위해 계속 읽어 같은 집합에 합친다.
ANONYMOUS_CHANNEL_ID: int | None = _get_int("ANONYMOUS_CHANNEL_ID")
ANONYMOUS_CHANNEL_IDS: frozenset[int] = frozenset(
    _get_int_list("ANONYMOUS_CHANNEL_IDS")
    + ([ANONYMOUS_CHANNEL_ID] if ANONYMOUS_CHANNEL_ID is not None else [])
)

# ---------------------------------------------------------------- 일정 알림(기존)
MEETING_NOTIFY_CHANNEL_ID: int | None = _get_int("MEETING_NOTIFY_CHANNEL_ID")

# ---------------------------------------------------------------- 신입 OT 수요조사
# 매주 정해진 요일/시각에 "같은 문구"를 올려 참가 희망자를 모으는 정기 공지.
# 최소 인원 같은 조건은 두지 않는다 — 반응을 보고 진행 여부는 사람이 판단한다.
OT_NOTICE_CHANNEL_ID: int | None = _get_int("OT_NOTICE_CHANNEL_ID", 1422581802479910994)
# 0=월 … 2=수 … 6=일 (datetime.weekday() 와 같은 기준)
OT_NOTICE_WEEKDAY: int = _get_int_req("OT_NOTICE_WEEKDAY", 2)
OT_NOTICE_HOUR: int = _get_int_req("OT_NOTICE_HOUR", 14)
OT_NOTICE_MINUTE: int = _get_int_req("OT_NOTICE_MINUTE", 0)
# 공지에 봇이 미리 달아둘 반응. 비워두면 반응을 달지 않는다.
OT_NOTICE_EMOJI: str = _get_str("OT_NOTICE_EMOJI", "<:keep_cghm:1507318397191589949>")
_DEFAULT_OT_NOTICE_MESSAGE = (
    "# 금일 9시 신입 오리엔테이션✨️\n"
    "## 참가를 희망하시는 신입분🐣 or 한 번도 안 들어보신 분은 미리 "
    "<:keep_cghm:1507318397191589949> 눌러주세요!"
)
# .env 로 덮어쓸 때는 줄바꿈을 `\n` 두 글자로 적는다.
_OT_NOTICE_MESSAGE_RAW = _get_str("OT_NOTICE_MESSAGE", _DEFAULT_OT_NOTICE_MESSAGE)
OT_NOTICE_MESSAGE: str = _OT_NOTICE_MESSAGE_RAW.replace("\\n", "\n")

# 수요조사에 미참가자가 반응하면 그날 이 시각으로 일정이 자동 등록된다("금일 9시").
OT_SCHEDULE_HOUR: int = _get_int_req("OT_SCHEDULE_HOUR", 21)
OT_SCHEDULE_MINUTE: int = _get_int_req("OT_SCHEDULE_MINUTE", 0)
OT_SCHEDULE_CONTENT: str = _get_str("OT_SCHEDULE_CONTENT", "신입 오리엔테이션")

# 수요조사 마감. 이 시각에 그때까지 모인 반응을 기준으로 "오늘 OT 하는지"를 한 번 공지한다.
# 마감 뒤에 눌린 반응도 21시 일정에는 계속 반영된다(공지만 이 시점 기준이다).
OT_DEADLINE_HOUR: int = _get_int_req("OT_DEADLINE_HOUR", 19)
OT_DEADLINE_MINUTE: int = _get_int_req("OT_DEADLINE_MINUTE", 0)
# 마감 공지를 올릴 채널. 비우면 일정 알림 채널(MEETING_NOTIFY_CHANNEL_ID)로 간다.
OT_DEADLINE_CHANNEL_ID: int | None = (
    _get_int("OT_DEADLINE_CHANNEL_ID") or MEETING_NOTIFY_CHANNEL_ID
)
OT_DEADLINE_PLACE: str = _get_str("OT_DEADLINE_PLACE", "Studio KEEP KEXCO")
# {time} = OT 시각(예: "21시"), {place} = OT_DEADLINE_PLACE
_DEFAULT_OT_DEADLINE_OPEN = "🐣 금일 {time} 신입 OT 진행합니다~\n장소 : {place}"
_DEFAULT_OT_DEADLINE_CLOSED = "🌙 오늘 신입 OT는 쉬어갑니다~"
OT_DEADLINE_OPEN_MESSAGE: str = _get_str(
    "OT_DEADLINE_OPEN_MESSAGE", _DEFAULT_OT_DEADLINE_OPEN
).replace("\\n", "\n")
OT_DEADLINE_CLOSED_MESSAGE: str = _get_str(
    "OT_DEADLINE_CLOSED_MESSAGE", _DEFAULT_OT_DEADLINE_CLOSED
).replace("\\n", "\n")

# 참가 이력 DB. "한 번이라도 OT 를 들었는가" 만 담는다(활동 관리 DB 와 별개).
OT_DB_PATH: str = _get_str("OT_DB_PATH", os.path.join(BASE_DIR, "data", "ot.db"))
# 최초 1회 스캔에서 '참가함' 처리에서 빼둘 기존 인원(= 아직 OT 를 안 들은 사람).
# 스캔은 딱 한 번만 돌므로, 그 뒤에 들어온 사람은 여기 적지 않아도 자동으로 OT 대상이다.
_DEFAULT_OT_SEED_EXCLUDE_IDS = (
    1545424556234121317,
    1306520775406260257,
    345744512095617024,
    1239615001065422865,
    865702970510409738,
    1107683583667208283,
    629636469576695808,
    317256743484784641,
)
OT_SEED_EXCLUDE_IDS: frozenset[int] = frozenset(
    _get_int_list("OT_SEED_EXCLUDE_IDS") or _DEFAULT_OT_SEED_EXCLUDE_IDS
)

# ---------------------------------------------------------------- FR-2 활동 관리
ACTIVITY_DB_PATH: str = _get_str(
    "ACTIVITY_DB_PATH", os.path.join(BASE_DIR, "data", "activity.db")
)
ACTIVITY_ADMIN_ROLE_ID: int | None = _get_int("ACTIVITY_ADMIN_ROLE_ID")
ACTIVITY_ADMIN_ROLE_NAME: str = _get_str("ACTIVITY_ADMIN_ROLE_NAME")
ACTIVITY_NOTIFY_CHANNEL_ID: int | None = _get_int("ACTIVITY_NOTIFY_CHANNEL_ID")
# 활동 기간. 만료일 = 마지막 활동일 + 이 값(일). 개월 차감 방식은 쓰지 않는다.
ACTIVITY_PERIOD_DAYS: int = _get_int("ACTIVITY_PERIOD_DAYS", 90) or 90
# 활동 카운트(마지막 활동일 = 1일째) 신호등. 이 일수를 '초과'하면 주황/빨강.
# 주황 기준을 넘긴 멤버는 ACTIVITY_NOTIFY_CHANNEL_ID 채널에 한 번 공지된다.
ACTIVITY_ORANGE_DAYS: int = _get_int("ACTIVITY_ORANGE_DAYS", 60) or 60
ACTIVITY_RED_DAYS: int = _get_int("ACTIVITY_RED_DAYS", 90) or 90

# ---------------------------------------------------------------- FR-6 반응 포워딩
TARGET_CHANNEL_ID: int | None = _get_int("TARGET_CHANNEL_ID")
# 감지할 채널 목록. 포럼 채널 ID를 넣으면 그 안의 모든 포스트(스레드)가 함께 감지된다.
# 비워두면 봇이 보는 모든 채널을 감지한다.
SOURCE_CHANNEL_IDS: frozenset[int] = frozenset(_get_int_list("SOURCE_CHANNEL_IDS"))
FORWARDED_DB_PATH: str = _get_str(
    "FORWARDED_DB_PATH", os.path.join(BASE_DIR, "forwarded.json")
)
REACTION_PIN_EMOJI: str = os.getenv("REACTION_PIN_EMOJI") or "⭐"
REACTION_MIN_COUNT: int = _get_int("REACTION_MIN_COUNT", 5) or 5
REACTION_SUPER_COUNT: int = _get_int("REACTION_SUPER_COUNT", 30) or 30


def validate() -> list[str]:
    """필수 설정 누락을 문자열 목록으로 반환한다(봇 기동 시 로그용)."""
    problems: list[str] = []
    if not TOKEN:
        problems.append("TOKEN 이 비어 있습니다. 봇을 기동할 수 없습니다.")
    if not DEV_USER_IDS:
        problems.append("DEV_USER_IDS 가 비어 있어 /리로드·/재시작 을 아무도 쓸 수 없습니다.")
    if FORUM_CHANNEL_ID is None:
        problems.append("FORUM_CHANNEL_ID 가 없어 유튜브 포럼 게시가 비활성화됩니다.")
    if not ANONYMOUS_CHANNEL_IDS:
        problems.append(
            "ANONYMOUS_CHANNEL_IDS / ANONYMOUS_CHANNEL_ID 가 모두 없어 익명 채팅이 비활성화됩니다."
        )
    if ACTIVITY_ADMIN_ROLE_ID is None and not ACTIVITY_ADMIN_ROLE_NAME:
        problems.append(
            "ACTIVITY_ADMIN_ROLE_ID / ACTIVITY_ADMIN_ROLE_NAME 이 모두 없어 활동 관리 명령어를 아무도 쓸 수 없습니다."
        )
    if OT_NOTICE_CHANNEL_ID is None:
        problems.append("OT_NOTICE_CHANNEL_ID 가 없어 신입 OT 수요조사 공지가 비활성화됩니다.")
    if ACTIVITY_NOTIFY_CHANNEL_ID is None:
        problems.append("ACTIVITY_NOTIFY_CHANNEL_ID 가 없어 활동 공지를 보낼 수 없습니다.")
    if not YOUTUBE_API_KEY or not YOUTUBE_CHANNEL_ID:
        problems.append("YOUTUBE_API_KEY / YOUTUBE_CHANNEL_ID 가 없어 업로드 감지가 비활성화됩니다.")
    return problems
