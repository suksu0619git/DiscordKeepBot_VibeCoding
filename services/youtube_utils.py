"""FR-3 유튜브 URL / 제목 처리 순수 함수 모음 (Discord 의존성 없음)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse

OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
# Discord 스레드(포럼 포스트) 이름 길이 제한
THREAD_NAME_LIMIT = 100


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def shorts_url(video_id: str) -> str:
    return f"https://www.youtube.com/shorts/{video_id}"


def oembed_request_url(video_url: str) -> str:
    """oEmbed 조회 URL. API 키가 필요 없다."""
    return f"{OEMBED_ENDPOINT}?{urlencode({'url': video_url, 'format': 'json'})}"


def is_shorts_url(url: str) -> bool:
    """URL 경로에 `/shorts/` 가 포함되어 있으면 쇼츠로 판별한다."""
    if not url:
        return False
    return "/shorts/" in urlparse(str(url)).path.rstrip("/") + "/"


def is_short_link(url: str) -> bool:
    """`youtu.be/...` 단축 링크인지 여부(최종 리다이렉트 재확인 대상)."""
    if not url:
        return False
    return urlparse(str(url)).netloc.lower().removeprefix("www.") == "youtu.be"


def extract_video_id(url: str) -> str | None:
    """유튜브 URL 에서 video id 를 추출한다. watch / youtu.be / shorts / embed 지원."""
    if not url:
        return None
    parsed = urlparse(str(url))
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path

    if host == "youtu.be":
        candidate = path.lstrip("/").split("/")[0]
        return candidate or None

    if host not in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        return None

    if path == "/watch":
        values = parse_qs(parsed.query).get("v")
        return values[0] if values else None

    for prefix in ("/shorts/", "/embed/", "/live/", "/v/"):
        if path.startswith(prefix):
            candidate = path[len(prefix) :].split("/")[0]
            return candidate or None
    return None


def truncate_thread_name(title: str, limit: int = THREAD_NAME_LIMIT) -> str:
    """포럼 포스트 제목을 Discord 제한(100자)에 맞게 자른다."""
    title = " ".join((title or "").split())  # 개행/연속 공백 정리
    if not title:
        return "(제목 없음)"
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "…"
