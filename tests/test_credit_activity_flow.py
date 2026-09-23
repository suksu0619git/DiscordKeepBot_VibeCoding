"""FR-1 크레딧 제출 → FR-2 DB 자동 갱신 통합 테스트 (디스코드 연결 불필요).

`ActivityTracker.apply_credit_activity()` 를 임시 DB에 대고 직접 호출해
FR-2.5(정확 일치 갱신 / 미등록 닉네임 안내 / credit_logs 기록)를 검증한다.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.activity import ActivityTracker  # noqa: E402
from services.activity_db import ActivityDB, now_kst  # noqa: E402
from services.credit_format import (  # noqa: E402
    CreditData,
    CreditPerson,
    parse_tags,
    people_from_names,
)


class DummyBot:
    """ActivityTracker 가 생성 시점에 요구하는 최소 인터페이스."""

    guilds: list = []

    def get_user(self, _user_id):
        return None

    def get_channel(self, _channel_id):
        return None


def sample_credit_data() -> CreditData:
    return CreditData(
        title="VRC Unity Noob vs Pro",
        production=people_from_names("AT_Cat, RUCOO"),
        edit=people_from_names("으름__"),
        three_d=people_from_names("suksu0619"),
        filming=people_from_names("AT_Cat"),
        act=people_from_names("잉어에요, 깜 냐 옹"),
        music_link="https://youtu.be/zql8G-4gE7w",
        world="Studio KEEP - Studio KEEP KEXCO",
        tags=parse_tags("vrchat, shorts"),
    )


class CreditToActivityFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tracker = ActivityTracker(DummyBot())
        # 실 DB 대신 임시 DB 를 쓰고, 일일 스캔 루프는 테스트에서 돌리지 않는다.
        self.tracker.expiration_scan.cancel()
        self.tracker.db = ActivityDB(os.path.join(self._tmpdir.name, "activity.db"))
        self.tracker._schema_ready = False
        await self.tracker.db.init_schema()
        self.tracker._schema_ready = True

    async def asyncTearDown(self):
        self._tmpdir.cleanup()

    async def test_registered_nicknames_are_renewed(self):
        db = self.tracker.db
        await db.register(101, "AT_Cat", 3, now=now_kst().replace(year=2026, month=1, day=1))
        await db.register(102, "으름__", 3, now=now_kst().replace(year=2026, month=1, day=1))
        before = await db.get(101)

        data = sample_credit_data()
        result = await self.tracker.apply_credit_activity(
            data.people_by_role(), data.title
        )

        after = await db.get(101)
        self.assertGreater(after.expire_date, before.expire_date)
        self.assertEqual(
            sorted(result.updated_nicknames), sorted(["AT_Cat", "으름__"])
        )

    async def test_unregistered_nicknames_are_reported(self):
        await self.tracker.db.register(101, "AT_Cat", 3)
        data = sample_credit_data()

        result = await self.tracker.apply_credit_activity(
            data.people_by_role(), data.title
        )

        self.assertEqual(result.updated_nicknames, ["AT_Cat"])
        self.assertEqual(
            result.unknown, ["RUCOO", "으름__", "suksu0619", "잉어에요", "깜 냐 옹"]
        )

    async def test_unknown_list_has_no_duplicates(self):
        # AT_Cat 은 Production/Filming 두 역할에 등장하지만 안내는 한 번만.
        data = sample_credit_data()
        result = await self.tracker.apply_credit_activity(
            data.people_by_role(), data.title
        )
        self.assertEqual(result.unknown.count("AT_Cat"), 1)

    async def test_credit_logs_record_every_role(self):
        await self.tracker.db.register(101, "AT_Cat", 3)
        data = sample_credit_data()
        await self.tracker.apply_credit_activity(data.people_by_role(), data.title)

        logs = await self.tracker.db.list_credit_logs(101)
        self.assertEqual(
            sorted(log.role_in_video for log in logs), ["Filming", "Production"]
        )
        self.assertTrue(all(log.video_title == "VRC Unity Noob vs Pro" for log in logs))

    async def test_warned_flag_is_reset_on_credit_submission(self):
        db = self.tracker.db
        await db.register(101, "AT_Cat", 3)
        await db.mark_warned([101])
        self.assertTrue((await db.get(101)).warned)

        data = sample_credit_data()
        await self.tracker.apply_credit_activity(data.people_by_role(), data.title)
        self.assertFalse((await db.get(101)).warned)

    async def test_nickname_with_internal_space_matches_exactly(self):
        await self.tracker.db.register(103, "깜 냐 옹", 3)
        data = sample_credit_data()
        result = await self.tracker.apply_credit_activity(
            data.people_by_role(), data.title
        )
        self.assertIn("깜 냐 옹", result.updated_nicknames)

    async def test_partial_name_does_not_match(self):
        await self.tracker.db.register(104, "AT_Cat_Official", 3)
        data = sample_credit_data()
        result = await self.tracker.apply_credit_activity(
            data.people_by_role(), data.title
        )
        self.assertEqual(result.updated_nicknames, [])
        self.assertIn("AT_Cat", result.unknown)

    async def test_expired_member_is_reactivated(self):
        db = self.tracker.db
        await db.register(101, "AT_Cat", 3, now=now_kst().replace(year=2020, month=1, day=1))
        await db.mark_expired()
        self.assertEqual((await db.get(101)).status, "expired")

        data = sample_credit_data()
        await self.tracker.apply_credit_activity(data.people_by_role(), data.title)
        self.assertEqual((await db.get(101)).status, "active")

    async def test_empty_submission_is_safe(self):
        result = await self.tracker.apply_credit_activity(
            {"Production": [], "Edit": [], "3D": [], "Filming": [], "Act": []}, "제목"
        )
        self.assertEqual(result.updated, [])
        self.assertEqual(result.unknown, [])


class CreditByUserIdTest(unittest.IsolatedAsyncioTestCase):
    """유저 선택 메뉴로 제출된 경우(= user_id 가 있는 경우)의 갱신."""

    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tracker = ActivityTracker(DummyBot())
        self.tracker.expiration_scan.cancel()
        self.tracker.db = ActivityDB(os.path.join(self._tmpdir.name, "activity.db"))
        self.tracker._schema_ready = False
        await self.tracker.db.init_schema()
        self.tracker._schema_ready = True

    async def asyncTearDown(self):
        self._tmpdir.cleanup()

    async def test_user_id_matches_even_when_display_name_differs(self):
        """개명의 핵심 이점: 표시 이름이 달라도 ID 로 찾는다."""
        await self.tracker.db.register(101, "AT_Cat", 3)

        result = await self.tracker.apply_credit_activity(
            {"Production": [CreditPerson(name="완전히_다른_새닉네임", user_id=101)]},
            "제목",
        )

        # DB 에 등록된 닉네임 기준으로 보고된다.
        self.assertEqual(result.updated_nicknames, ["AT_Cat"])
        self.assertEqual(result.unknown, [])

    async def test_same_name_different_user_is_not_matched(self):
        """이름이 같아도 ID 가 다르면 남의 활동을 갱신하지 않는다."""
        await self.tracker.db.register(101, "AT_Cat", 3)
        before = await self.tracker.db.get(101)

        result = await self.tracker.apply_credit_activity(
            {"Production": [CreditPerson(name="AT_Cat", user_id=999)]}, "제목"
        )

        self.assertEqual(result.updated, [])
        self.assertEqual((await self.tracker.db.get(101)).expire_date, before.expire_date)
        # 닉네임이 선점돼 있어 자동 등록도 못 한다 → 충돌로 보고.
        self.assertEqual([p.user_id for p in result.conflict_people], [999])
        # 충돌은 미등록으로 중복 집계되지 않는다.
        self.assertEqual(result.unknown, [])
        self.assertIsNone(await self.tracker.db.get(999))

    async def test_credit_logs_are_written_for_each_role(self):
        await self.tracker.db.register(101, "AT_Cat", 3)
        person = CreditPerson(name="AT_Cat", user_id=101)

        await self.tracker.apply_credit_activity(
            {"Production": [person], "Filming": [person]}, "VRC Unity Noob vs Pro"
        )

        logs = await self.tracker.db.list_credit_logs(101)
        self.assertEqual(
            sorted(log.role_in_video for log in logs), ["Filming", "Production"]
        )

    async def test_falls_back_to_nickname_when_user_id_missing(self):
        """user_id 가 없는 경로(과거 방식)도 그대로 동작해야 한다."""
        await self.tracker.db.register(101, "AT_Cat", 3)

        result = await self.tracker.apply_credit_activity(
            {"Production": [CreditPerson(name="AT_Cat")]}, "제목"
        )

        self.assertEqual(result.updated_nicknames, ["AT_Cat"])

    async def test_duplicate_person_across_roles_registered_once(self):
        person = CreditPerson(name="신규", user_id=777)
        result = await self.tracker.apply_credit_activity(
            {"Production": [person], "Edit": [person]}, "제목"
        )
        self.assertEqual(len(result.registered_people), 1)
        # 등록은 한 번이지만 역할별 활동 기록은 둘 다 남는다.
        logs = await self.tracker.db.list_credit_logs(777)
        self.assertEqual(
            sorted(log.role_in_video for log in logs), ["Edit", "Production"]
        )


class CreditAutoRegisterTest(unittest.IsolatedAsyncioTestCase):
    """크레딧에 오른 미등록 인원의 자동 등록."""

    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tracker = ActivityTracker(DummyBot())
        self.tracker.expiration_scan.cancel()
        self.tracker.db = ActivityDB(os.path.join(self._tmpdir.name, "activity.db"))
        self.tracker._schema_ready = False
        await self.tracker.db.init_schema()
        self.tracker._schema_ready = True

    async def asyncTearDown(self):
        self._tmpdir.cleanup()

    async def test_new_member_is_registered_with_full_period(self):
        result = await self.tracker.apply_credit_activity(
            {"Production": [CreditPerson(name="신입", user_id=500)]}, "제목"
        )

        self.assertEqual(result.registered_nicknames, ["신입"])
        record = await self.tracker.db.get(500)
        self.assertIsNotNone(record)
        self.assertEqual(record.nickname, "신입")
        self.assertEqual(record.status, "active")
        self.assertFalse(record.warned)
        self.assertGreater(record.remaining().total_days, 0)

    async def test_auto_registered_member_gets_credit_log(self):
        await self.tracker.apply_credit_activity(
            {"Edit": [CreditPerson(name="신입", user_id=500)]}, "영상제목"
        )
        logs = await self.tracker.db.list_credit_logs(500)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].role_in_video, "Edit")
        self.assertEqual(logs[0].video_title, "영상제목")

    async def test_existing_member_is_not_reported_as_registered(self):
        await self.tracker.db.register(101, "AT_Cat", 3)
        result = await self.tracker.apply_credit_activity(
            {"Production": [CreditPerson(name="AT_Cat", user_id=101)]}, "제목"
        )
        self.assertEqual(result.registered_people, [])
        self.assertEqual(result.updated_nicknames, ["AT_Cat"])

    async def test_nickname_without_user_id_is_not_auto_registered(self):
        """닉네임만 있으면 어느 계정인지 알 수 없어 등록할 수 없다."""
        result = await self.tracker.apply_credit_activity(
            {"Production": [CreditPerson(name="이름만있음")]}, "제목"
        )
        self.assertEqual(result.registered_people, [])
        self.assertEqual(result.unknown, ["이름만있음"])

    async def test_registration_uses_display_name_at_submission(self):
        await self.tracker.apply_credit_activity(
            {"Act": [CreditPerson(name="선택당시닉", user_id=501)]}, "제목"
        )
        self.assertEqual((await self.tracker.db.get(501)).nickname, "선택당시닉")


if __name__ == "__main__":
    unittest.main()
