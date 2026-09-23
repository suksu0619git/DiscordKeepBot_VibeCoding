"""신입 OT 참가 이력 데이터 계층.

"이 사람이 OT 를 한 번이라도 들었는가" 하나만 기록한다. 활동 기간(activity.db) 과는
목적이 다르므로 DB 파일을 따로 쓴다(`data/ot.db`).

판정 규칙은 단순하다.

* 행이 있고 `attended = 1` → **참가함** (수요조사에 반응해도 일정이 잡히지 않는다)
* 행이 없거나 `attended = 0` → **미참가** (반응하면 OT 가 열린다)

행이 없으면 미참가이므로, 최초 1회 시딩(`seed`) 이후에 새로 들어온 사람은 아무것도
하지 않아도 자동으로 "OT 대상" 이 된다. 반대로 시딩 시점에 서버에 있던 기존 인원은
전부 참가함으로 찍어 두고, 예외로 지정된 사람만 미참가로 남긴다.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Iterable

import aiosqlite

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# source 컬럼에 들어가는 값 — 누가/무엇이 이 상태를 만들었는지 나중에 알아보려고 남긴다.
SOURCE_SEED = "seed"  # 최초 스캔으로 참가함 처리된 기존 인원
SOURCE_SEED_EXCLUDED = "seed-excluded"  # 최초 스캔에서 예외로 지정돼 미참가로 남은 사람
SOURCE_OT = "ot"  # OT 가 실제로 열려서 참가 처리된 사람
SOURCE_MANUAL = "manual"  # 관리자가 손으로 고친 경우

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ot_attendance (
    user_id    INTEGER PRIMARY KEY,
    attended   INTEGER NOT NULL DEFAULT 1,
    source     TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ot_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

META_SEEDED_AT = "seeded_at"  # 최초 스캔 완료 시각(있으면 다시 스캔하지 않는다)
META_NOTICE_MESSAGE_ID = "notice_message_id"  # 지금 유효한 수요조사 메시지
META_NOTICE_CHANNEL_ID = "notice_channel_id"
META_NOTICE_DATE = "notice_date"  # YYYY-MM-DD (그날 저녁 일정으로 잡는다)


def now_kst() -> datetime:
    return datetime.now(KST)


@dataclass(frozen=True)
class AttendanceRecord:
    user_id: int
    attended: bool
    source: str
    updated_at: str

    @classmethod
    def from_row(cls, row) -> "AttendanceRecord":
        return cls(
            user_id=row["user_id"],
            attended=bool(row["attended"]),
            source=row["source"] or "",
            updated_at=row["updated_at"],
        )


class OtAttendanceDB:
    """ot_attendance / ot_meta 테이블 접근자. 호출마다 커넥션을 열고 닫는다."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def init_schema(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with self._connect() as conn:
            await conn.executescript(_SCHEMA)
            await conn.commit()
        logger.info("신입 OT 참가 이력 DB 준비 완료: %s", self.db_path)

    # ------------------------------------------------------------- 조회
    async def get(self, user_id: int) -> AttendanceRecord | None:
        async with self._connect() as conn:
            async with conn.execute(
                "SELECT * FROM ot_attendance WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return AttendanceRecord.from_row(row) if row else None

    async def is_attended(self, user_id: int) -> bool:
        """행이 없으면 미참가(False). 새로 들어온 사람이 자동으로 OT 대상이 되는 근거다."""
        record = await self.get(user_id)
        return bool(record and record.attended)

    async def attended_user_ids(self) -> set[int]:
        async with self._connect() as conn:
            async with conn.execute(
                "SELECT user_id FROM ot_attendance WHERE attended = 1"
            ) as cursor:
                rows = await cursor.fetchall()
        return {row["user_id"] for row in rows}

    # ------------------------------------------------------------- 변경
    async def set_attended(
        self,
        user_id: int,
        attended: bool,
        source: str = SOURCE_MANUAL,
        now: datetime | None = None,
    ) -> None:
        await self.set_attended_many([user_id], attended, source, now)

    async def set_attended_many(
        self,
        user_ids: Iterable[int],
        attended: bool,
        source: str = SOURCE_MANUAL,
        now: datetime | None = None,
    ) -> int:
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return 0
        stamp = (now or now_kst()).isoformat()
        async with self._connect() as conn:
            await conn.executemany(
                "INSERT INTO ot_attendance (user_id, attended, source, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " attended = excluded.attended, source = excluded.source,"
                " updated_at = excluded.updated_at",
                [(user_id, int(attended), source, stamp) for user_id in ids],
            )
            await conn.commit()
        return len(ids)

    # ------------------------------------------------------------- 최초 스캔
    async def is_seeded(self) -> bool:
        return await self.get_meta(META_SEEDED_AT) is not None

    async def seed(
        self,
        attended_ids: Iterable[int],
        excluded_ids: Iterable[int] = (),
        now: datetime | None = None,
    ) -> tuple[int, int]:
        """서버 기존 인원을 참가함으로, 예외 지정 인원을 미참가로 한 번에 찍는다.

        이미 시딩했으면 아무것도 하지 않고 (0, 0) 을 돌려준다. 두 번 돌면 그사이
        새로 들어온 사람까지 "참가함" 이 되어 버리므로 **반드시 1회만** 실행한다.
        """
        if await self.is_seeded():
            return (0, 0)

        now = now or now_kst()
        excluded = list(dict.fromkeys(excluded_ids))
        excluded_set = set(excluded)
        attended = [uid for uid in dict.fromkeys(attended_ids) if uid not in excluded_set]

        await self.set_attended_many(attended, True, SOURCE_SEED, now)
        await self.set_attended_many(excluded, False, SOURCE_SEED_EXCLUDED, now)
        await self.set_meta(META_SEEDED_AT, now.isoformat())
        logger.info(
            "신입 OT 참가 이력 최초 스캔 완료: 참가함 %d명 · 예외(미참가) %d명",
            len(attended),
            len(excluded),
        )
        return (len(attended), len(excluded))

    # ------------------------------------------------------------- 메타
    async def get_meta(self, key: str) -> str | None:
        async with self._connect() as conn:
            async with conn.execute(
                "SELECT value FROM ot_meta WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
        return row["value"] if row else None

    async def get_meta_int(self, key: str) -> int | None:
        raw = await self.get_meta(key)
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            logger.warning("ot_meta.%s 값이 정수가 아닙니다: %r", key, raw)
            return None

    async def set_meta(self, key: str, value: str | int | None) -> None:
        async with self._connect() as conn:
            if value is None:
                await conn.execute("DELETE FROM ot_meta WHERE key = ?", (key,))
            else:
                await conn.execute(
                    "INSERT INTO ot_meta (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
            await conn.commit()
