"""FR-2 닉네임 활동 관리용 데이터 계층.

- 모든 DB 접근은 `aiosqlite` 로 처리한다(동기 sqlite3 금지).
- 날짜는 KST 기준 tz-aware `datetime`, 저장은 ISO 8601 문자열.
- 만료일은 **마지막 활동일 + N일** 로만 계산한다(개월 차감 방식 아님).
  일 단위라서 달마다 길이가 달라지는 문제나 월말 edge case 가 아예 없다.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 크레딧에서 쓰이는 역할 명칭(credit_logs.role_in_video 에 저장되는 값)
CREDIT_ROLES = ("Production", "Edit", "3D", "Filming", "Act")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    user_id      INTEGER PRIMARY KEY,
    nickname     TEXT NOT NULL,
    last_active  TEXT NOT NULL,
    expire_date  TEXT NOT NULL,
    warned       INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS credit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    video_title   TEXT,
    submitted_at  TEXT,
    role_in_video TEXT
);

CREATE INDEX IF NOT EXISTS idx_members_nickname ON members (nickname);
CREATE INDEX IF NOT EXISTS idx_credit_logs_user ON credit_logs (user_id);
"""


# --------------------------------------------------------------------------- #
# 날짜 유틸 (순수 함수 — 단위 테스트 대상)
# --------------------------------------------------------------------------- #
def now_kst() -> datetime:
    return datetime.now(KST)


def add_days(base: datetime, days: int) -> datetime:
    """`base` 에 `days` 일을 더한다(만료일 계산의 유일한 규칙).

    >>> add_days(datetime(2025, 11, 30, tzinfo=KST), 90).date().isoformat()
    '2026-02-28'
    """
    return base + timedelta(days=days)


def to_iso(value: datetime) -> str:
    """tz-aware ISO 8601 문자열로 직렬화한다(naive 입력은 KST로 간주)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=KST)
    return value.isoformat()


def from_iso(value: str) -> datetime:
    """ISO 8601 문자열을 tz-aware datetime 으로 복원한다."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed


@dataclass(frozen=True)
class Remaining:
    """잔여 기간 표현. 기준은 오직 '남은 일수' 하나다."""

    total_days: int  # 음수면 이미 만료

    @property
    def expired(self) -> bool:
        return self.total_days < 0

    def format_ko(self) -> str:
        """관리자용 상세 표기. 일수를 그대로 보여주고 주 단위를 괄호로 덧붙인다.

        >>> Remaining(90).format_ko()
        '90일 남음 (약 12주)'
        >>> Remaining(-5).format_ko()
        '만료됨 (5일 지남)'
        """
        if self.expired:
            return f"만료됨 ({-self.total_days}일 지남)"
        weeks = self.total_days // 7
        if weeks:
            return f"{self.total_days}일 남음 (약 {weeks}주)"
        return f"{self.total_days}일 남음"

    def format_weeks_ko(self) -> str:
        """만료까지 남은 기간을 '주' 중심으로 표현한다(채널 공지 문구용).

        만료 임박 알림은 남은 일수가 얼마 없을 때 나가므로 주 단위가 읽기 쉽다.

        >>> Remaining(21).format_weeks_ko()
        '3주 남았습니다'
        >>> Remaining(13).format_weeks_ko()
        '1주 6일 남았습니다'
        >>> Remaining(3).format_weeks_ko()
        '3일 남았습니다'
        """
        if self.expired:
            return "만료되었습니다"
        if self.total_days == 0:
            return "오늘 만료됩니다"
        weeks, rest_days = divmod(self.total_days, 7)
        if weeks and rest_days:
            return f"{weeks}주 {rest_days}일 남았습니다"
        if weeks:
            return f"{weeks}주 남았습니다"
        return f"{rest_days}일 남았습니다"


def remaining_breakdown(expire_date: datetime, now: datetime | None = None) -> Remaining:
    """만료일까지 남은 일수를 계산한다(시각은 무시하고 날짜끼리 뺀다)."""
    now = now or now_kst()
    return Remaining(total_days=(expire_date.date() - now.date()).days)


def elapsed_days(last_active: datetime, now: datetime | None = None) -> int:
    """마지막 활동일로부터 지난 일수(시각은 무시하고 날짜끼리 뺀다).

    잔여 일수와 달리 이 값은 `ACTIVITY_PERIOD_DAYS` 설정과 무관하다. 기간 설정을
    바꿔도 "언제 마지막으로 활동했는가"는 그대로 읽히므로 조회 화면의 주 지표로 쓴다.

    >>> elapsed_days(datetime(2026, 7, 30, tzinfo=KST), now=datetime(2026, 8, 16, tzinfo=KST))
    17
    """
    now = now or now_kst()
    return (now.date() - last_active.date()).days


def format_elapsed_ko(days: int) -> str:
    """경과 일수 표기. 일수를 그대로 보여주고 주 단위를 괄호로 덧붙인다.

    >>> format_elapsed_ko(0)
    '오늘 활동'
    >>> format_elapsed_ko(17)
    '+17일 (약 2주)'
    >>> format_elapsed_ko(3)
    '+3일'
    """
    if days <= 0:
        return "오늘 활동"
    weeks = days // 7
    if weeks:
        return f"+{days}일 (약 {weeks}주)"
    return f"+{days}일"


# --------------------------------------------------------------------------- #
# 레코드
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MemberRecord:
    user_id: int
    nickname: str
    last_active: datetime
    expire_date: datetime
    warned: bool
    status: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "MemberRecord":
        return cls(
            user_id=row["user_id"],
            nickname=row["nickname"],
            last_active=from_iso(row["last_active"]),
            expire_date=from_iso(row["expire_date"]),
            warned=bool(row["warned"]),
            status=row["status"] or "active",
        )

    def remaining(self, now: datetime | None = None) -> Remaining:
        return remaining_breakdown(self.expire_date, now)

    def elapsed(self, now: datetime | None = None) -> int:
        """마지막 활동일로부터 지난 일수."""
        return elapsed_days(self.last_active, now)


@dataclass(frozen=True)
class CreditLogRecord:
    video_title: str
    submitted_at: datetime
    role_in_video: str


class NicknameTakenError(Exception):
    """다른 user_id 가 이미 같은 닉네임을 쓰고 있을 때."""

    def __init__(self, owner_id: int):
        super().__init__(f"nickname already owned by {owner_id}")
        self.owner_id = owner_id


# --------------------------------------------------------------------------- #
# DAO
# --------------------------------------------------------------------------- #
class ActivityDB:
    """members / credit_logs 테이블 접근자.

    호출마다 커넥션을 열고 닫는다(봇 트래픽 규모에서 충분하며, 장기 커넥션을
    Cog 리로드 사이에 남기지 않는다).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        # aiosqlite.Connection 은 한 번만 await 할 수 있으므로(내부 Thread 재시작 불가)
        # 반드시 connect() 결과를 그대로 async with 에 넘긴다.
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
        logger.info("활동 관리 DB 준비 완료: %s", self.db_path)

    # ------------------------------------------------------------- 조회
    async def get(self, user_id: int) -> MemberRecord | None:
        async with self._connect() as conn:
            async with conn.execute(
                "SELECT * FROM members WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return MemberRecord.from_row(row) if row else None

    async def get_by_nickname(self, nickname: str) -> MemberRecord | None:
        async with self._connect() as conn:
            async with conn.execute(
                "SELECT * FROM members WHERE nickname = ?", (nickname.strip(),)
            ) as cursor:
                row = await cursor.fetchone()
        return MemberRecord.from_row(row) if row else None

    async def list_all(self) -> list[MemberRecord]:
        """만료 임박순(expire_date 오름차순)으로 전체 조회."""
        async with self._connect() as conn:
            async with conn.execute(
                "SELECT * FROM members ORDER BY expire_date ASC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [MemberRecord.from_row(row) for row in rows]

    # ------------------------------------------------------------- 변경
    async def register(
        self,
        user_id: int,
        nickname: str,
        days: int,
        now: datetime | None = None,
    ) -> bool:
        """신규 등록. 이미 등록된 user_id 면 False.

        Raises:
            NicknameTakenError: 같은 닉네임을 다른 user_id 가 이미 사용 중.
        """
        nickname = nickname.strip()
        now = now or now_kst()
        existing_nick = await self.get_by_nickname(nickname)
        if existing_nick and existing_nick.user_id != user_id:
            raise NicknameTakenError(existing_nick.user_id)

        async with self._connect() as conn:
            async with conn.execute(
                "SELECT 1 FROM members WHERE user_id = ?", (user_id,)
            ) as cursor:
                if await cursor.fetchone():
                    return False
            await conn.execute(
                "INSERT INTO members (user_id, nickname, last_active, expire_date, warned, status)"
                " VALUES (?, ?, ?, ?, 0, 'active')",
                (user_id, nickname, to_iso(now), to_iso(add_days(now, days))),
            )
            await conn.commit()
        return True

    async def renew(
        self, user_id: int, days: int, now: datetime | None = None
    ) -> MemberRecord | None:
        """마지막 활동일을 오늘로 찍고 만료일을 오늘 + days 로 다시 잡는다(warned 리셋)."""
        now = now or now_kst()
        async with self._connect() as conn:
            cursor = await conn.execute(
                "UPDATE members SET last_active = ?, expire_date = ?, warned = 0,"
                " status = 'active' WHERE user_id = ?",
                (to_iso(now), to_iso(add_days(now, days)), user_id),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                return None
        return await self.get(user_id)

    async def set_days(
        self, user_id: int, days: int, now: datetime | None = None
    ) -> MemberRecord | None:
        """잔여 일수를 임의 지정한다(만료일 = 오늘 + days). last_active 는 건드리지 않는다."""
        now = now or now_kst()
        async with self._connect() as conn:
            cursor = await conn.execute(
                "UPDATE members SET expire_date = ?, warned = 0, status = 'active'"
                " WHERE user_id = ?",
                (to_iso(add_days(now, days)), user_id),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                return None
        return await self.get(user_id)

    async def update_nickname(self, user_id: int, nickname: str) -> bool:
        nickname = nickname.strip()
        existing = await self.get_by_nickname(nickname)
        if existing and existing.user_id != user_id:
            raise NicknameTakenError(existing.user_id)
        async with self._connect() as conn:
            cursor = await conn.execute(
                "UPDATE members SET nickname = ? WHERE user_id = ?", (nickname, user_id)
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def delete(self, user_id: int) -> bool:
        async with self._connect() as conn:
            cursor = await conn.execute(
                "DELETE FROM members WHERE user_id = ?", (user_id,)
            )
            await conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------- FR-2.5 자동 갱신
    async def touch_by_user_id(
        self,
        user_id: int,
        video_title: str,
        role_in_video: str,
        days: int,
        now: datetime | None = None,
    ) -> MemberRecord | None:
        """user_id 로 활동을 갱신하고 credit_logs 에 기록한다.

        갱신 = last_active 를 지금으로, expire_date 를 `last_active + days` 로 다시 쓴다.

        크레딧 모달이 유저 선택 메뉴로 바뀌면서 이쪽이 기본 경로가 되었다. 닉네임
        문자열에 의존하지 않으므로 개명이나 표기 차이에 영향을 받지 않는다.
        등록되지 않은 user_id 면 아무 것도 하지 않고 None 을 반환한다.
        """
        record = await self.get(user_id)
        if record is None:
            return None

        now = now or now_kst()
        async with self._connect() as conn:
            await conn.execute(
                "UPDATE members SET last_active = ?, expire_date = ?, warned = 0,"
                " status = 'active' WHERE user_id = ?",
                (to_iso(now), to_iso(add_days(now, days)), user_id),
            )
            await conn.execute(
                "INSERT INTO credit_logs (user_id, video_title, submitted_at, role_in_video)"
                " VALUES (?, ?, ?, ?)",
                (user_id, video_title, to_iso(now), role_in_video),
            )
            await conn.commit()
        return await self.get(user_id)

    async def touch_by_nickname(
        self,
        nickname: str,
        video_title: str,
        role_in_video: str,
        days: int,
        now: datetime | None = None,
    ) -> MemberRecord | None:
        """닉네임(공백 trim 후 정확 일치)으로 활동을 갱신한다.

        user_id 를 알 수 없는 경로를 위한 보조 수단이다. 닉네임을 user_id 로 바꾼 뒤
        `touch_by_user_id` 에 위임하므로 갱신 로직은 한 곳에만 존재한다.
        등록되지 않은 닉네임이면 아무 것도 하지 않고 None 을 반환한다.
        """
        nickname = nickname.strip()
        if not nickname:
            return None
        record = await self.get_by_nickname(nickname)
        if record is None:
            return None
        return await self.touch_by_user_id(
            user_id=record.user_id,
            video_title=video_title,
            role_in_video=role_in_video,
            days=days,
            now=now,
        )

    async def list_credit_logs(self, user_id: int, limit: int = 20) -> list[CreditLogRecord]:
        async with self._connect() as conn:
            async with conn.execute(
                "SELECT video_title, submitted_at, role_in_video FROM credit_logs"
                " WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            CreditLogRecord(
                video_title=row["video_title"] or "",
                submitted_at=from_iso(row["submitted_at"]),
                role_in_video=row["role_in_video"] or "",
            )
            for row in rows
        ]

    # ------------------------------------------------------------- FR-2.6 자동 알림
    async def list_pending_warnings(
        self, within_days: int, now: datetime | None = None
    ) -> list[MemberRecord]:
        """만료가 `within_days` 일 이내이고 아직 경고하지 않은(warned=0) 멤버."""
        now = now or now_kst()
        threshold = to_iso(now + timedelta(days=within_days))
        async with self._connect() as conn:
            async with conn.execute(
                "SELECT * FROM members WHERE warned = 0 AND expire_date <= ?"
                " ORDER BY expire_date ASC",
                (threshold,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [MemberRecord.from_row(row) for row in rows]

    async def mark_warned(self, user_ids: list[int]) -> None:
        if not user_ids:
            return
        async with self._connect() as conn:
            await conn.executemany(
                "UPDATE members SET warned = 1 WHERE user_id = ?",
                [(uid,) for uid in user_ids],
            )
            await conn.commit()

    async def mark_expired(self, now: datetime | None = None) -> list[int]:
        """만료일이 지난 멤버의 status 를 'expired' 로 동기화하고 대상 ID를 반환."""
        now = now or now_kst()
        stamp = to_iso(now)
        async with self._connect() as conn:
            async with conn.execute(
                "SELECT user_id FROM members WHERE expire_date <= ? AND status != 'expired'",
                (stamp,),
            ) as cursor:
                rows = await cursor.fetchall()
            user_ids = [row["user_id"] for row in rows]
            if user_ids:
                await conn.executemany(
                    "UPDATE members SET status = 'expired' WHERE user_id = ?",
                    [(uid,) for uid in user_ids],
                )
                await conn.commit()
        return user_ids
