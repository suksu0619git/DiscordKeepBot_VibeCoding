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

# ---------------------------------------------------------------- FR-2 활동 관리
ACTIVITY_DB_PATH: str = _get_str(
    "ACTIVITY_DB_PATH", os.path.join(BASE_DIR, "data", "activity.db")
)
ACTIVITY_ADMIN_ROLE_ID: int | None = _get_int("ACTIVITY_ADMIN_ROLE_ID")
ACTIVITY_ADMIN_ROLE_NAME: str = _get_str("ACTIVITY_ADMIN_ROLE_NAME")
ACTIVITY_NOTIFY_CHANNEL_ID: int | None = _get_int("ACTIVITY_NOTIFY_CHANNEL_ID")
# 활동 기간. 만료일 = 마지막 활동일 + 이 값(일). 개월 차감 방식은 쓰지 않는다.
ACTIVITY_PERIOD_DAYS: int = _get_int("ACTIVITY_PERIOD_DAYS", 90) or 90
EXPIRATION_WARN_DAYS: int = _get_int("EXPIRATION_WARN_DAYS", 21) or 21

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
    if ACTIVITY_NOTIFY_CHANNEL_ID is None:
        problems.append("ACTIVITY_NOTIFY_CHANNEL_ID 가 없어 만료 임박 알림을 보낼 수 없습니다.")
    if not YOUTUBE_API_KEY or not YOUTUBE_CHANNEL_ID:
        problems.append("YOUTUBE_API_KEY / YOUTUBE_CHANNEL_ID 가 없어 업로드 감지가 비활성화됩니다.")
    return problems
