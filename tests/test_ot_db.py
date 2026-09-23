"""신입 OT 참가 이력 DB 단위 테스트.

핵심 규칙 두 가지를 못 박는다.

* 행이 없으면 미참가 → 시딩 이후 새로 들어온 사람은 자동으로 OT 대상이 된다.
* 시딩은 1회뿐 → 두 번 돌면 그사이 들어온 신입까지 참가함이 되어버린다.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ot_db import (  # noqa: E402
    META_NOTICE_MESSAGE_ID,
    SOURCE_SEED,
    SOURCE_SEED_EXCLUDED,
    OtAttendanceDB,
)


async def fresh_db() -> OtAttendanceDB:
    db = OtAttendanceDB(os.path.join(tempfile.mkdtemp(), "ot.db"))
    await db.init_schema()
    return db


class SeedTest(unittest.IsolatedAsyncioTestCase):
    async def test_seed_marks_existing_members_and_keeps_excluded_pending(self):
        db = await fresh_db()
        attended, excluded = await db.seed([1, 2, 3], [3, 4])

        self.assertEqual((attended, excluded), (2, 2))
        self.assertTrue(await db.is_attended(1))
        self.assertFalse(await db.is_attended(3))  # 예외 지정
        self.assertFalse(await db.is_attended(4))  # 서버에 없어도 미참가로 기록
        self.assertEqual((await db.get(1)).source, SOURCE_SEED)
        self.assertEqual((await db.get(3)).source, SOURCE_SEED_EXCLUDED)

    async def test_seed_runs_only_once(self):
        db = await fresh_db()
        await db.seed([1])
        self.assertEqual(await db.seed([2]), (0, 0))
        self.assertFalse(await db.is_attended(2))  # 나중에 들어온 사람은 미참가 그대로

    async def test_unknown_user_is_pending(self):
        db = await fresh_db()
        await db.seed([1])
        self.assertFalse(await db.is_attended(99999))


class UpdateTest(unittest.IsolatedAsyncioTestCase):
    async def test_set_attended_is_upsert(self):
        db = await fresh_db()
        await db.set_attended(7, True, "ot")
        self.assertTrue(await db.is_attended(7))
        await db.set_attended(7, False)
        self.assertFalse(await db.is_attended(7))

    async def test_set_attended_many_deduplicates(self):
        db = await fresh_db()
        self.assertEqual(await db.set_attended_many([5, 5, 6], True, "ot"), 2)
        self.assertEqual(await db.attended_user_ids(), {5, 6})


class MetaTest(unittest.IsolatedAsyncioTestCase):
    async def test_meta_roundtrip_and_delete(self):
        db = await fresh_db()
        self.assertIsNone(await db.get_meta_int(META_NOTICE_MESSAGE_ID))
        await db.set_meta(META_NOTICE_MESSAGE_ID, 4242)
        self.assertEqual(await db.get_meta_int(META_NOTICE_MESSAGE_ID), 4242)
        await db.set_meta(META_NOTICE_MESSAGE_ID, None)
        self.assertIsNone(await db.get_meta(META_NOTICE_MESSAGE_ID))


if __name__ == "__main__":
    unittest.main()
