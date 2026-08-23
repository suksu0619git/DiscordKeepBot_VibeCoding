"""FR-3 유튜브 업로드 감지 → 포럼 채널 자동 게시.

기존 동작(텍스트 채널에 Embed 알림)을 **포럼 포스트 생성**으로 대체한다.

- 포스트 제목 = 유튜브 영상 제목 (oEmbed 조회, API 키 불필요)
- 포스트 태그 = 롱폼 / 쇼츠 자동 구분
- 포스트 내용 = 유튜브 링크

업로드 감지 자체는 기존과 동일하게 YouTube Data API 의 uploads 플레이리스트를
폴링한다(폴링에는 항상 `watch?v=` URL 이 오므로 쇼츠 여부는 URL 로 알 수 없다).
그래서 `youtube.com/shorts/{id}` 를 요청해 **최종 리다이렉트 URL** 로 판별한다.
쇼츠가 아니면 유튜브가 `/watch` 로 리다이렉트한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import aiohttp
import discord
from discord.ext import commands, tasks

import config
from services.youtube_utils import (
    THREAD_NAME_LIMIT,
    extract_video_id,
    is_short_link,
    is_shorts_url,
    oembed_request_url,
    shorts_url,
    truncate_thread_name,
    watch_url,
)

logger = logging.getLogger(__name__)

PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
# 상태 파일에 남길 최근 게시 영상 ID 개수
POSTED_HISTORY_LIMIT = 50


class YouTubeForumNotifier(commands.Cog):
    """유튜브 신규 업로드를 포럼 포스트로 게시한다."""

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.api_key = config.YOUTUBE_API_KEY
        self.channel_id = config.YOUTUBE_CHANNEL_ID
        # 채널 ID 의 'UC' 를 'UU' 로 바꾸면 업로드 목록 플레이리스트 ID 가 된다.
        self.uploads_playlist_id = (
            self.channel_id.replace("UC", "UU", 1) if self.channel_id else ""
        )
        self.state_path = config.YOUTUBE_STATE_PATH
        self.last_video_id: str | None = None
        self.posted_video_ids: list[str] = []
        self._state_loaded = False

        if not self.api_key or not self.channel_id:
            logger.warning(
                "YOUTUBE_API_KEY / YOUTUBE_CHANNEL_ID 가 없어 업로드 감지를 시작하지 않습니다."
            )
        else:
            self.check_youtube.change_interval(
                minutes=config.YOUTUBE_CHECK_INTERVAL_MINUTES
            )
            self.check_youtube.start()

    def cog_unload(self):
        self.check_youtube.cancel()

    # ------------------------------------------------------------------ #
    # 상태 저장 (재시작 후 같은 영상을 다시 게시하지 않도록)
    # ------------------------------------------------------------------ #
    def _read_state_file(self) -> dict:
        if not os.path.exists(self.state_path):
            return {}
        with open(self.state_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _write_state_file(self, data: dict) -> None:
        parent = os.path.dirname(os.path.abspath(self.state_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    async def _load_state(self) -> None:
        if self._state_loaded:
            return
        try:
            # 파일 I/O 는 스레드로 넘겨 이벤트 루프를 블로킹하지 않는다.
            data = await asyncio.to_thread(self._read_state_file)
            self.last_video_id = data.get("last_video_id")
            self.posted_video_ids = list(data.get("posted_video_ids", []))
            logger.info(
                "유튜브 상태 로드: last_video_id=%s (게시 기록 %d건)",
                self.last_video_id,
                len(self.posted_video_ids),
            )
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("유튜브 상태 파일 로드 실패 (%s): %s", self.state_path, exc)
        self._state_loaded = True

    async def _save_state(self) -> None:
        payload = {
            "last_video_id": self.last_video_id,
            "posted_video_ids": self.posted_video_ids[-POSTED_HISTORY_LIMIT:],
        }
        try:
            await asyncio.to_thread(self._write_state_file, payload)
        except OSError as exc:
            logger.error("유튜브 상태 파일 저장 실패 (%s): %s", self.state_path, exc)

    # ------------------------------------------------------------------ #
    # 유튜브 조회
    # ------------------------------------------------------------------ #
    async def _fetch_latest_upload(
        self, session: aiohttp.ClientSession
    ) -> tuple[str, str] | None:
        """uploads 플레이리스트에서 최신 (video_id, title) 을 가져온다."""
        params = {
            "part": "snippet",
            "playlistId": self.uploads_playlist_id,
            "maxResults": "1",
            "key": self.api_key,
        }
        async with session.get(PLAYLIST_ITEMS_URL, params=params) as response:
            if response.status == 403:
                logger.error(
                    "유튜브 API 403: 할당량 초과 또는 권한 문제. YouTube Data API v3 활성화 여부를 확인하세요."
                )
                return None
            if response.status != 200:
                logger.error("유튜브 API 오류: status=%s", response.status)
                return None

            data = await response.json()
            items = data.get("items") or []
            if not items:
                logger.info("업로드 플레이리스트가 비어 있습니다.")
                return None

            snippet = items[0].get("snippet", {})
            video_id = (snippet.get("resourceId") or {}).get("videoId")
            if not video_id:
                logger.warning("최신 항목에서 videoId 를 찾지 못했습니다.")
                return None
            return video_id, snippet.get("title") or ""

    async def fetch_oembed_title(
        self, session: aiohttp.ClientSession, video_url: str
    ) -> str | None:
        """oEmbed 로 영상 제목을 조회한다(API 키 불필요). 실패 시 None."""
        request_url = oembed_request_url(video_url)
        try:
            async with session.get(request_url) as response:
                if response.status != 200:
                    # 비공개/삭제/연령제한 영상은 401/403/404 를 반환한다.
                    logger.warning(
                        "oEmbed 제목 조회 실패 (status=%s): %s", response.status, video_url
                    )
                    return None
                data = await response.json(content_type=None)
                title = (data or {}).get("title")
                return title or None
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            logger.warning("oEmbed 요청 중 오류 (%s): %s", video_url, exc)
            return None

    async def resolve_final_url(
        self, session: aiohttp.ClientSession, url: str
    ) -> str | None:
        """리다이렉트를 따라간 최종 URL 을 반환한다."""
        try:
            async with session.get(url, allow_redirects=True) as response:
                return str(response.url)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("최종 URL 확인 실패 (%s): %s", url, exc)
            return None

    async def detect_shorts(
        self, session: aiohttp.ClientSession, video_id: str, source_url: str = ""
    ) -> bool | None:
        """쇼츠 여부를 판별한다. 판별 불가 시 None.

        1차: 원본 URL 경로에 `/shorts/` 가 있으면 즉시 쇼츠.
        2차: `youtu.be` 단축 링크거나 `watch?v=` 형태면 `/shorts/{id}` 로 요청해
             최종 리다이렉트 URL 을 확인한다(쇼츠가 아니면 `/watch` 로 이동).
        """
        if source_url and is_shorts_url(source_url):
            return True

        if source_url and is_short_link(source_url):
            resolved = await self.resolve_final_url(session, source_url)
            if resolved and is_shorts_url(resolved):
                return True

        final_url = await self.resolve_final_url(session, shorts_url(video_id))
        if final_url is None:
            return None
        return is_shorts_url(final_url)

    # ------------------------------------------------------------------ #
    # 포럼 게시
    # ------------------------------------------------------------------ #
    async def _get_forum_channel(self) -> discord.ForumChannel | None:
        channel_id = config.FORUM_CHANNEL_ID
        if channel_id is None:
            logger.error("FORUM_CHANNEL_ID 가 설정되지 않아 포럼 게시를 할 수 없습니다.")
            return None

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                logger.error("포럼 채널 조회 실패 (%s): %s", channel_id, exc)
                return None

        if not isinstance(channel, discord.ForumChannel):
            logger.error(
                "FORUM_CHANNEL_ID(%s) 는 포럼 채널이 아닙니다 (실제 타입: %s).",
                channel_id,
                type(channel).__name__,
            )
            return None
        return channel

    @staticmethod
    def _pick_tag(
        forum: discord.ForumChannel, wanted: str
    ) -> discord.ForumTag | None:
        """available_tags 를 순회해 태그를 찾는다. 설정값이 숫자면 태그 ID로 매칭."""
        if not wanted:
            return None
        if wanted.isdigit():
            wanted_id = int(wanted)
            for tag in forum.available_tags:
                if tag.id == wanted_id:
                    return tag
        for tag in forum.available_tags:
            if tag.name == wanted:
                return tag
        # 대소문자/공백 차이는 허용한다.
        normalized = wanted.strip().casefold()
        for tag in forum.available_tags:
            if tag.name.strip().casefold() == normalized:
                return tag
        return None

    async def create_forum_post(
        self, title: str, video_url: str, shorts: bool | None
    ) -> discord.Thread | None:
        """포럼에 포스트를 생성한다. 태그가 없으면 태그 없이 생성하고 경고 로그."""
        forum = await self._get_forum_channel()
        if forum is None:
            return None

        wanted = (
            config.YOUTUBE_SHORTS_TAG_NAME if shorts else config.YOUTUBE_LONGFORM_TAG_NAME
        )
        applied_tags: list[discord.ForumTag] = []
        if shorts is None:
            logger.warning("쇼츠/롱폼 판별에 실패해 태그 없이 게시합니다: %s", video_url)
        else:
            tag = self._pick_tag(forum, wanted)
            if tag is None:
                logger.warning(
                    "포럼 '%s' 에서 태그 %r 를 찾지 못해 태그 없이 게시합니다."
                    " (사용 가능한 태그: %s)",
                    forum.name,
                    wanted,
                    [t.name for t in forum.available_tags] or "없음",
                )
            else:
                applied_tags.append(tag)

        try:
            thread = await forum.create_thread(
                name=truncate_thread_name(title, THREAD_NAME_LIMIT),
                content=video_url,
                applied_tags=applied_tags or None,
            )
        except discord.Forbidden:
            logger.error(
                "권한 없음: 포럼 채널(%s)에 포스트를 만들 수 없습니다."
                " 봇에게 '스레드 만들기'/'메시지 보내기' 권한이 필요합니다.",
                forum.id,
            )
            return None
        except discord.HTTPException as exc:
            logger.error("포럼 포스트 생성 실패: %s", exc)
            return None

        logger.info(
            "포럼 포스트 생성: %r (%s) 태그=%s",
            thread.name,
            video_url,
            [t.name for t in applied_tags] or "없음",
        )
        return thread

    # ------------------------------------------------------------------ #
    # 폴링 루프
    # ------------------------------------------------------------------ #
    @tasks.loop(minutes=5)
    async def check_youtube(self):
        try:
            await self._load_state()
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                latest = await self._fetch_latest_upload(session)
                if latest is None:
                    return
                video_id, api_title = latest

                if video_id == self.last_video_id:
                    return

                first_run = self.last_video_id is None and not self.posted_video_ids
                self.last_video_id = video_id
                if first_run:
                    # 첫 실행에서는 기준점만 저장하고 과거 영상을 게시하지 않는다.
                    logger.info("첫 실행: 기준 영상 ID만 저장합니다 (%s)", video_id)
                    await self._save_state()
                    return

                if video_id in self.posted_video_ids:
                    logger.info("이미 게시한 영상입니다. 건너뜁니다: %s", video_id)
                    await self._save_state()
                    return

                video_url = watch_url(video_id)
                title = await self.fetch_oembed_title(session, video_url)
                if title is None:
                    # oEmbed 실패(비공개/삭제 등) → 폴링 API 제목 → 링크만
                    title = api_title or video_url
                    logger.warning(
                        "oEmbed 제목을 쓸 수 없어 대체 제목으로 게시합니다: %r", title
                    )
                shorts = await self.detect_shorts(session, video_id, video_url)

            thread = await self.create_forum_post(title, video_url, shorts)
            if thread is not None:
                self.posted_video_ids.append(video_id)
            await self._save_state()
        except Exception:
            # 루프 안에서 예외가 새면 태스크가 죽으므로 반드시 잡아서 기록한다.
            logger.exception("유튜브 업로드 확인 중 오류")

    @check_youtube.before_loop
    async def before_check_youtube(self):
        await self.bot.wait_until_ready()


def setup(bot: discord.Bot):
    bot.add_cog(YouTubeForumNotifier(bot))
