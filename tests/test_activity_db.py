"""FR-2 날짜 연산 / DAO 단위 테스트 (봇 기동 불필요).

실행: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.activity_db import (  # noqa: E402
    KST,
    ActivityDB,
    NicknameTakenError,
    add_days,
    elapsed_days,
    elapsed_light,
    format_elapsed_ko,
    from_iso,
    remaining_breakdown,
    to_iso,
)

# 활동 기간 기본값(config.ACTIVITY_PERIOD_DAYS)과 같은 값으로 테스트한다.
PERIOD = 90


def dt(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=KST)


class AddDaysTest(unittest.TestCase):
    def test_plain_day_addition(self):
        self.assertEqual(add_days(dt(2026, 1, 15), 90).date().isoformat(), "2026-04-15")

    def test_month_length_does_not_matter(self):
        # 일 단위 계산이므로 짧은 달을 지나도 그대로 90일 뒤가 된다.
        self.assertEqual(add_days(dt(2025, 11, 30), 90).date().isoformat(), "2026-02-28")

    def test_leap_day_is_counted(self):
        # 2024-02-29 가 실제로 하루로 세어져 평년보다 하루 뒤가 된다.
        self.assertEqual(add_days(dt(2023, 11, 30), 90).date().isoformat(), "2024-02-28")

    def test_year_rollover(self):
        self.assertEqual(add_days(dt(2026, 11, 15), 90).date().isoformat(), "2027-02-13")

    def test_timezone_is_preserved(self):
        self.assertEqual(add_days(dt(2026, 1, 15), 90).tzinfo, KST)

    def test_zero_days(self):
        self.assertEqual(add_days(dt(2026, 5, 20), 0).date().isoformat(), "2026-05-20")


class IsoRoundTripTest(unittest.TestCase):
    def test_round_trip_keeps_value(self):
        original = dt(2026, 7, 30, 9)
        self.assertEqual(from_iso(to_iso(original)), original)

    def test_naive_input_is_treated_as_kst(self):
        naive = datetime(2026, 7, 30, 9)
        self.assertEqual(from_iso(to_iso(naive)).tzinfo, KST)

    def test_naive_stored_value_is_restored_as_kst(self):
        self.assertEqual(from_iso("2026-07-30T09:00:00").tzinfo, KST)


class RemainingBreakdownTest(unittest.TestCase):
    def test_full_period_remaining(self):
        rem = remaining_breakdown(dt(2026, 10, 28), now=dt(2026, 7, 30))
        self.assertEqual(rem.total_days, 90)
        self.assertFalse(rem.expired)
        self.assertEqual(rem.format_ko(), "90일 남음 (약 12주)")

    def test_partial_period(self):
        rem = remaining_breakdown(dt(2026, 9, 12), now=dt(2026, 7, 30))
        self.assertEqual(rem.total_days, 44)
        self.assertFalse(rem.expired)

    def test_time_of_day_is_ignored(self):
        # 만료일 새벽 vs 오늘 밤 — 날짜만 비교하므로 아직 만료가 아니다.
        rem = remaining_breakdown(dt(2026, 7, 30, 1), now=dt(2026, 7, 30, 23))
        self.assertEqual(rem.total_days, 0)
        self.assertFalse(rem.expired)
        self.assertEqual(rem.format_ko(), "0일 남음")

    def test_expired_reports_elapsed_days(self):
        rem = remaining_breakdown(dt(2026, 6, 15), now=dt(2026, 7, 30))
        self.assertTrue(rem.expired)
        self.assertEqual(rem.total_days, -45)
        self.assertEqual(rem.format_ko(), "만료됨 (45일 지남)")

    def test_less_than_a_week_omits_weeks(self):
        rem = remaining_breakdown(dt(2026, 8, 5), now=dt(2026, 7, 30))
        self.assertEqual(rem.format_ko(), "6일 남음")


class ElapsedTest(unittest.TestCase):
    """조회 화면 카운트: 마지막 활동으로부터 +N일 과 신호등."""

    def test_last_active_day_counts_as_day_one(self):
        self.assertEqual(elapsed_days(dt(2026, 7, 30, 9), now=dt(2026, 7, 30, 23)), 1)
        self.assertEqual(format_elapsed_ko(1), "+1일")
        self.assertEqual(format_elapsed_ko(17), "+17일")

    def test_counts_up_one_per_day(self):
        self.assertEqual(elapsed_days(dt(2026, 7, 30), now=dt(2026, 7, 31)), 2)

    def test_expire_date_is_over_90(self):
        # 만료일(마지막 활동 +90일) 당일 카운트는 91 → 빨간불.
        self.assertEqual(elapsed_days(dt(2026, 7, 30), now=dt(2026, 10, 28)), 91)
        self.assertEqual(elapsed_light(91, 60, 90), "🔴")

    def test_light_is_green_up_to_60_days(self):
        self.assertEqual(elapsed_light(0, 60, 90), "🟢")
        self.assertEqual(elapsed_light(60, 60, 90), "🟢")

    def test_light_is_orange_over_60_days(self):
        self.assertEqual(elapsed_light(61, 60, 90), "🟠")
        self.assertEqual(elapsed_light(90, 60, 90), "🟠")

    def test_light_is_red_over_90_days(self):
        self.assertEqual(elapsed_light(91, 60, 90), "🔴")


class ActivityDBTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = ActivityDB(os.path.join(self._tmpdir.name, "nested", "activity.db"))
        await self.db.init_schema()

    async def asyncTearDown(self):
        self._tmpdir.cleanup()

    async def test_init_schema_is_idempotent(self):
        await self.db.init_schema()  # 두 번 호출해도 예외 없음
        self.assertTrue(os.path.exists(self.db.db_path))

    async def test_register_and_get(self):
        created = await self.db.register(1, "  suksu0619 ", PERIOD, now=dt(2026, 7, 30))
        self.assertTrue(created)
        record = await self.db.get(1)
        self.assertIsNotNone(record)
        self.assertEqual(record.nickname, "suksu0619")  # 공백은 trim 되어 저장
        self.assertEqual(record.last_active.date().isoformat(), "2026-07-30")
        # 만료일 = 마지막 활동일 + 90일
        self.assertEqual(record.expire_date.date().isoformat(), "2026-10-28")
        self.assertFalse(record.warned)
        self.assertEqual(record.status, "active")

    async def test_duplicate_register_returns_false(self):
        await self.db.register(1, "AT_Cat", PERIOD)
        self.assertFalse(await self.db.register(1, "AT_Cat", PERIOD))

    async def test_nickname_taken_by_other_user_raises(self):
        await self.db.register(1, "AT_Cat", PERIOD)
        with self.assertRaises(NicknameTakenError) as caught:
            await self.db.register(2, "AT_Cat", PERIOD)
        self.assertEqual(caught.exception.owner_id, 1)

    async def test_get_by_nickname_trims_query(self):
        await self.db.register(1, "으름__", PERIOD)
        self.assertIsNotNone(await self.db.get_by_nickname("  으름__  "))
        self.assertIsNone(await self.db.get_by_nickname("으름"))  # 부분 일치는 안 됨

    async def test_list_all_is_sorted_by_expire_date(self):
        await self.db.register(1, "a", 90, now=dt(2026, 1, 1))
        await self.db.register(2, "b", 30, now=dt(2026, 1, 1))
        await self.db.register(3, "c", 180, now=dt(2026, 1, 1))
        self.assertEqual([r.nickname for r in await self.db.list_all()], ["b", "a", "c"])

    async def test_renew_resets_warned_and_restarts_count(self):
        await self.db.register(1, "a", PERIOD, now=dt(2026, 1, 1))
        await self.db.mark_warned([1])
        self.assertTrue((await self.db.get(1)).warned)

        record = await self.db.renew(1, PERIOD, now=dt(2026, 7, 30))
        self.assertIsNotNone(record)
        self.assertFalse(record.warned)
        self.assertEqual(record.last_active.date().isoformat(), "2026-07-30")
        self.assertEqual(record.expire_date.date().isoformat(), "2026-10-28")

    async def test_renew_unknown_member_returns_none(self):
        self.assertIsNone(await self.db.renew(999, PERIOD))

    async def test_set_days_keeps_last_active(self):
        await self.db.register(1, "a", PERIOD, now=dt(2026, 1, 1))
        record = await self.db.set_days(1, 180, now=dt(2026, 7, 30))
        self.assertEqual(record.expire_date.date().isoformat(), "2027-01-26")
        self.assertEqual(record.last_active.date().isoformat(), "2026-01-01")

    async def test_delete(self):
        await self.db.register(1, "a", PERIOD)
        self.assertTrue(await self.db.delete(1))
        self.assertFalse(await self.db.delete(1))
        self.assertIsNone(await self.db.get(1))

    async def test_touch_by_nickname_updates_and_logs(self):
        await self.db.register(1, "AT_Cat", PERIOD, now=dt(2026, 1, 1))
        await self.db.mark_warned([1])

        record = await self.db.touch_by_nickname(
            "  AT_Cat  ", "VRC Unity Noob vs Pro", "Production", PERIOD, now=dt(2026, 7, 30)
        )
        self.assertIsNotNone(record)
        self.assertFalse(record.warned)
        self.assertEqual(record.last_active.date().isoformat(), "2026-07-30")
        self.assertEqual(record.expire_date.date().isoformat(), "2026-10-28")

        logs = await self.db.list_credit_logs(1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].video_title, "VRC Unity Noob vs Pro")
        self.assertEqual(logs[0].role_in_video, "Production")

    async def test_touch_by_unknown_nickname_is_noop(self):
        self.assertIsNone(await self.db.touch_by_nickname("없는사람", "t", "Edit", PERIOD))
        self.assertIsNone(await self.db.touch_by_nickname("   ", "t", "Edit", PERIOD))

    async def test_touch_by_user_id_updates_and_logs(self):
        await self.db.register(1, "AT_Cat", PERIOD, now=dt(2026, 1, 1))
        await self.db.mark_warned([1])

        record = await self.db.touch_by_user_id(
            1, "VRC Unity Noob vs Pro", "Production", PERIOD, now=dt(2026, 7, 30)
        )
        self.assertIsNotNone(record)
        self.assertFalse(record.warned)
        self.assertEqual(record.expire_date.date().isoformat(), "2026-10-28")

        logs = await self.db.list_credit_logs(1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].role_in_video, "Production")

    async def test_touch_by_user_id_ignores_nickname_changes(self):
        """닉네임이 바뀌어도 같은 user_id 면 계속 갱신된다."""
        await self.db.register(1, "AT_Cat", PERIOD, now=dt(2026, 1, 1))
        await self.db.update_nickname(1, "새이름")

        record = await self.db.touch_by_user_id(1, "v", "Edit", PERIOD, now=dt(2026, 7, 30))
        self.assertEqual(record.nickname, "새이름")
        self.assertEqual(record.expire_date.date().isoformat(), "2026-10-28")

    async def test_touch_by_unknown_user_id_is_noop(self):
        self.assertIsNone(await self.db.touch_by_user_id(999, "t", "Edit", PERIOD))

    async def test_touch_records_one_log_per_role(self):
        await self.db.register(1, "AT_Cat", PERIOD)
        await self.db.touch_by_nickname("AT_Cat", "video", "Production", PERIOD)
        await self.db.touch_by_nickname("AT_Cat", "video", "Filming", PERIOD)
        logs = await self.db.list_credit_logs(1)
        self.assertEqual({log.role_in_video for log in logs}, {"Production", "Filming"})

    async def test_list_pending_warnings_is_strictly_over_the_count(self):
        now = dt(2026, 7, 30)
        await self.db.register(1, "day61", PERIOD, now=dt(2026, 5, 31))  # +61일째
        await self.db.register(2, "day60", PERIOD, now=dt(2026, 6, 1))  # +60일째
        await self.db.register(3, "warned", PERIOD, now=dt(2026, 5, 1))
        await self.db.mark_warned([3])

        pending = await self.db.list_pending_warnings(60, now=now)
        self.assertEqual([r.nickname for r in pending], ["day61"])

    async def test_list_pending_warnings_includes_already_expired_oldest_first(self):
        now = dt(2026, 7, 30)
        await self.db.register(1, "day61", PERIOD, now=dt(2026, 5, 31))
        await self.db.register(2, "gone", PERIOD, now=dt(2026, 1, 1))
        pending = await self.db.list_pending_warnings(60, now=now)
        self.assertEqual([r.nickname for r in pending], ["gone", "day61"])

    async def test_mark_warned_prevents_duplicate_notification(self):
        now = dt(2026, 7, 30)
        await self.db.register(1, "old", PERIOD, now=dt(2026, 5, 5))
        first = await self.db.list_pending_warnings(60, now=now)
        await self.db.mark_warned([r.user_id for r in first])
        second = await self.db.list_pending_warnings(60, now=now)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    async def test_mark_warned_with_empty_list_is_safe(self):
        await self.db.mark_warned([])

    async def test_mark_expired_syncs_status(self):
        now = dt(2026, 7, 30)
        await self.db.register(1, "gone", PERIOD, now=dt(2026, 1, 1))
        await self.db.register(2, "alive", PERIOD, now=now)
        self.assertEqual(await self.db.mark_expired(now=now), [1])
        self.assertEqual((await self.db.get(1)).status, "expired")
        self.assertEqual((await self.db.get(2)).status, "active")
        # 이미 expired 인 멤버는 다시 반환하지 않는다.
        self.assertEqual(await self.db.mark_expired(now=now), [])

    async def test_concurrent_access_does_not_deadlock(self):
        await asyncio.gather(*(self.db.register(i, f"user{i}", PERIOD) for i in range(10)))
        self.assertEqual(len(await self.db.list_all()), 10)


if __name__ == "__main__":
    unittest.main()
