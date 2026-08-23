"""FR-3 유튜브 URL 판별 / 제목 자르기 / 포럼 태그 매칭 단위 테스트.

네트워크와 디스코드 연결 없이 동작한다(HTTP 응답은 가짜 객체로 대체).
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.youtube_utils import (  # noqa: E402
    THREAD_NAME_LIMIT,
    extract_video_id,
    is_short_link,
    is_shorts_url,
    oembed_request_url,
    shorts_url,
    truncate_thread_name,
    watch_url,
)


class UrlBuilderTest(unittest.TestCase):
    def test_watch_url(self):
        self.assertEqual(watch_url("abc123"), "https://www.youtube.com/watch?v=abc123")

    def test_shorts_url(self):
        self.assertEqual(shorts_url("abc123"), "https://www.youtube.com/shorts/abc123")

    def test_oembed_url_has_json_format_and_encoded_target(self):
        url = oembed_request_url("https://www.youtube.com/watch?v=abc123")
        self.assertTrue(url.startswith("https://www.youtube.com/oembed?"))
        self.assertIn("format=json", url)
        self.assertIn("url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc123", url)

    def test_oembed_url_requires_no_api_key(self):
        self.assertNotIn("key=", oembed_request_url(watch_url("abc123")))


class IsShortsUrlTest(unittest.TestCase):
    def test_shorts_path_is_detected(self):
        self.assertTrue(is_shorts_url("https://www.youtube.com/shorts/abc123"))

    def test_shorts_path_with_query(self):
        self.assertTrue(is_shorts_url("https://www.youtube.com/shorts/abc123?feature=share"))

    def test_watch_path_is_not_shorts(self):
        self.assertFalse(is_shorts_url("https://www.youtube.com/watch?v=abc123"))

    def test_shorts_in_query_only_is_not_shorts(self):
        # 경로가 아닌 쿼리에 shorts 가 들어간 경우는 쇼츠가 아니다.
        self.assertFalse(is_shorts_url("https://www.youtube.com/watch?v=abc&t=/shorts/"))

    def test_shorts_in_video_title_slug_is_not_matched(self):
        self.assertFalse(is_shorts_url("https://www.youtube.com/watch?v=shorts"))

    def test_youtu_be_link_is_not_shorts_by_path(self):
        self.assertFalse(is_shorts_url("https://youtu.be/abc123"))

    def test_empty_and_none_safe(self):
        self.assertFalse(is_shorts_url(""))
        self.assertFalse(is_shorts_url(None))


class IsShortLinkTest(unittest.TestCase):
    def test_youtu_be_is_short_link(self):
        self.assertTrue(is_short_link("https://youtu.be/abc123"))

    def test_www_youtu_be_is_short_link(self):
        self.assertTrue(is_short_link("https://www.youtu.be/abc123"))

    def test_full_domain_is_not_short_link(self):
        self.assertFalse(is_short_link("https://www.youtube.com/watch?v=abc123"))

    def test_empty_safe(self):
        self.assertFalse(is_short_link(""))


class ExtractVideoIdTest(unittest.TestCase):
    def test_watch_url(self):
        self.assertEqual(extract_video_id("https://www.youtube.com/watch?v=abc123"), "abc123")

    def test_watch_url_with_extra_params(self):
        self.assertEqual(
            extract_video_id("https://www.youtube.com/watch?v=abc123&t=42s"), "abc123"
        )

    def test_short_link(self):
        self.assertEqual(extract_video_id("https://youtu.be/abc123"), "abc123")

    def test_short_link_with_query(self):
        self.assertEqual(
            extract_video_id("https://youtu.be/zql8G-4gE7w?si=NSFlwxqvd9kIZb0i"),
            "zql8G-4gE7w",
        )

    def test_shorts_url(self):
        self.assertEqual(extract_video_id("https://www.youtube.com/shorts/abc123"), "abc123")

    def test_embed_and_live_urls(self):
        self.assertEqual(extract_video_id("https://www.youtube.com/embed/abc123"), "abc123")
        self.assertEqual(extract_video_id("https://www.youtube.com/live/abc123"), "abc123")

    def test_mobile_host(self):
        self.assertEqual(extract_video_id("https://m.youtube.com/watch?v=abc123"), "abc123")

    def test_non_youtube_url_returns_none(self):
        self.assertIsNone(extract_video_id("https://vimeo.com/12345"))

    def test_channel_url_returns_none(self):
        self.assertIsNone(extract_video_id("https://www.youtube.com/@studiokeep"))

    def test_empty_returns_none(self):
        self.assertIsNone(extract_video_id(""))


class TruncateThreadNameTest(unittest.TestCase):
    def test_short_title_unchanged(self):
        self.assertEqual(truncate_thread_name("VRC Unity Noob vs Pro"), "VRC Unity Noob vs Pro")

    def test_exactly_at_limit_unchanged(self):
        title = "가" * THREAD_NAME_LIMIT
        self.assertEqual(truncate_thread_name(title), title)

    def test_over_limit_is_truncated_with_ellipsis(self):
        result = truncate_thread_name("가" * 150)
        self.assertEqual(len(result), THREAD_NAME_LIMIT)
        self.assertTrue(result.endswith("…"))

    def test_newlines_are_collapsed(self):
        self.assertEqual(truncate_thread_name("첫 줄\n둘째 줄"), "첫 줄 둘째 줄")

    def test_consecutive_spaces_are_collapsed(self):
        self.assertEqual(truncate_thread_name("a    b"), "a b")

    def test_blank_title_gets_placeholder(self):
        self.assertEqual(truncate_thread_name("   "), "(제목 없음)")
        self.assertEqual(truncate_thread_name(""), "(제목 없음)")


# --------------------------------------------------------------------------- #
# 포럼 태그 매칭 (_pick_tag) — discord.ForumTag 대신 최소 스텁을 사용
# --------------------------------------------------------------------------- #
@dataclass
class FakeTag:
    id: int
    name: str


@dataclass
class FakeForum:
    available_tags: list


class PickTagTest(unittest.TestCase):
    """`_pick_tag` 은 태그 ID 와 이름 양쪽으로 매칭되어야 한다.

    현재 .env 에는 태그 '이름' 자리에 19자리 태그 ID가 들어있어 두 방식 모두 필요하다.
    """

    def setUp(self):
        from cogs.youtube import YouTubeForumNotifier

        self.pick = YouTubeForumNotifier._pick_tag
        self.forum = FakeForum(
            available_tags=[
                FakeTag(id=1488104075861164072, name="롱폼"),
                FakeTag(id=1488104034043957318, name="쇼츠"),
            ]
        )

    def test_match_by_name(self):
        self.assertEqual(self.pick(self.forum, "쇼츠").id, 1488104034043957318)

    def test_match_by_tag_id_string(self):
        self.assertEqual(self.pick(self.forum, "1488104075861164072").name, "롱폼")

    def test_match_is_case_and_space_insensitive(self):
        forum = FakeForum(available_tags=[FakeTag(id=1, name="Shorts")])
        self.assertIsNotNone(self.pick(forum, " shorts "))

    def test_missing_tag_returns_none(self):
        self.assertIsNone(self.pick(self.forum, "없는태그"))

    def test_unknown_numeric_id_returns_none(self):
        self.assertIsNone(self.pick(self.forum, "9999999999999999999"))

    def test_empty_wanted_returns_none(self):
        self.assertIsNone(self.pick(self.forum, ""))

    def test_empty_available_tags_returns_none(self):
        self.assertIsNone(self.pick(FakeForum(available_tags=[]), "쇼츠"))


# --------------------------------------------------------------------------- #
# detect_shorts — 가짜 세션으로 리다이렉트 판별 로직 검증
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, url: str):
        self.url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """`get()` 이 요청 URL → 최종 URL 매핑대로 응답하는 가짜 세션."""

    def __init__(self, redirects: dict[str, str]):
        self.redirects = redirects
        self.requested: list[str] = []

    def get(self, url: str, **_kwargs):
        self.requested.append(url)
        return FakeResponse(self.redirects.get(url, url))


class BrokenResponse:
    """요청 시 네트워크 오류를 내는 응답 스텁."""

    async def __aenter__(self):
        raise aiohttp.ClientError("boom")

    async def __aexit__(self, *args):
        return False


class BrokenSession:
    def get(self, _url: str, **_kwargs):
        return BrokenResponse()


class DetectShortsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from cogs.youtube import YouTubeForumNotifier

        # __init__ 을 거치지 않고 메서드만 사용한다(폴링 루프 시작 방지).
        self.cog = YouTubeForumNotifier.__new__(YouTubeForumNotifier)

    async def test_source_url_with_shorts_path_short_circuits(self):
        session = FakeSession({})
        result = await self.cog.detect_shorts(
            session, "abc123", "https://www.youtube.com/shorts/abc123"
        )
        self.assertTrue(result)
        self.assertEqual(session.requested, [])  # 네트워크 요청 없이 판별

    async def test_watch_url_that_stays_on_shorts_is_shorts(self):
        # /shorts/{id} 요청이 그대로 유지되면 쇼츠
        session = FakeSession(
            {"https://www.youtube.com/shorts/abc123": "https://www.youtube.com/shorts/abc123"}
        )
        self.assertTrue(
            await self.cog.detect_shorts(session, "abc123", watch_url("abc123"))
        )

    async def test_watch_url_redirected_to_watch_is_longform(self):
        # 쇼츠가 아니면 유튜브가 /watch 로 리다이렉트한다.
        session = FakeSession(
            {"https://www.youtube.com/shorts/abc123": "https://www.youtube.com/watch?v=abc123"}
        )
        self.assertFalse(
            await self.cog.detect_shorts(session, "abc123", watch_url("abc123"))
        )

    async def test_youtu_be_link_is_resolved_first(self):
        session = FakeSession(
            {"https://youtu.be/abc123": "https://www.youtube.com/shorts/abc123"}
        )
        self.assertTrue(
            await self.cog.detect_shorts(session, "abc123", "https://youtu.be/abc123")
        )
        self.assertEqual(session.requested, ["https://youtu.be/abc123"])

    async def test_youtu_be_link_resolving_to_watch_falls_through_to_probe(self):
        session = FakeSession(
            {
                "https://youtu.be/abc123": "https://www.youtube.com/watch?v=abc123",
                "https://www.youtube.com/shorts/abc123": "https://www.youtube.com/watch?v=abc123",
            }
        )
        self.assertFalse(
            await self.cog.detect_shorts(session, "abc123", "https://youtu.be/abc123")
        )
        self.assertEqual(len(session.requested), 2)

    async def test_network_failure_returns_none(self):
        result = await self.cog.detect_shorts(
            BrokenSession(), "abc123", watch_url("abc123")
        )
        self.assertIsNone(result)  # 판별 불가 → 태그 없이 게시


if __name__ == "__main__":
    unittest.main()
