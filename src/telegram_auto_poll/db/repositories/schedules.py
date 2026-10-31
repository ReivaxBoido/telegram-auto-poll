"""Schedule repository: recurrence rows and the next-run handoff."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import Schedule
from ...domain.enums import ScheduleKind
from ..models import ScheduleRow
from .common import (
    dumps,
    format_date,
    format_time,
    from_epoch,
    load_int_list,
    parse_date,
    parse_time,
    to_epoch,
)


def _to_schedule(row: ScheduleRow) -> Schedule:
    return Schedule(
        poll_id=row.poll_id,
        kind=ScheduleKind(row.kind),
        timezone=row.timezone,
        time_of_day=parse_time(row.time_of_day),
        weekdays=tuple(load_int_list(row.weekdays_json)) if row.weekdays_json else (),
        day_of_month=row.day_of_month,
        run_at=from_epoch(row.run_at_utc),
        start_date=parse_date(row.starts_on_local_date),
        end_date=parse_date(row.ends_on_local_date),
        is_paused=bool(row.is_paused),
        next_run_at=from_epoch(row.next_run_at_utc),
        last_run_at=from_epoch(row.last_run_at_utc),
        id=row.id,
        created_at=from_epoch(row.created_at),
    )


class ScheduleRepository:
    """Reads and writes ``schedules``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, schedule: Schedule, *, now: datetime) -> Schedule:
        stamp = to_epoch(now)
        row = ScheduleRow(
            poll_id=schedule.poll_id,
            kind=str(schedule.kind),
            time_of_day=format_time(schedule.time_of_day),
            weekdays_json=dumps(list(schedule.weekdays)) if schedule.weekdays else None,
            day_of_month=schedule.day_of_month,
            run_at_utc=to_epoch(schedule.run_at),
            timezone=schedule.timezone,
            starts_on_local_date=format_date(schedule.start_date),
            ends_on_local_date=format_date(schedule.end_date),
            is_paused=1 if schedule.is_paused else 0,
            next_run_at_utc=to_epoch(schedule.next_run_at),
            last_run_at_utc=to_epoch(schedule.last_run_at),
            created_at=stamp,
            updated_at=stamp,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_schedule(row)

    async def get(self, schedule_id: int) -> Schedule | None:
        row = await self._session.get(ScheduleRow, schedule_id)
        return None if row is None else _to_schedule(row)

    async def list_all(
        self,
        *,
        poll_id: int | None = None,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Schedule]:
        statement = select(ScheduleRow).order_by(ScheduleRow.id).limit(limit).offset(offset)
        if poll_id is not None:
            statement = statement.where(ScheduleRow.poll_id == poll_id)
        if active_only:
            statement = statement.where(ScheduleRow.is_paused == 0)
        rows = (await self._session.scalars(statement)).all()
        return [_to_schedule(row) for row in rows]

    async def active_runs(self, *, limit: int = 10_000) -> list[tuple[int, datetime]]:
        """Return ``(schedule_id, next_run_at)`` for every active, upcoming schedule."""
        statement = (
            select(ScheduleRow.id, ScheduleRow.next_run_at_utc)
            .where(ScheduleRow.is_paused == 0)
            .where(ScheduleRow.next_run_at_utc.is_not(None))
            .order_by(ScheduleRow.id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return [(int(row[0]), datetime.fromtimestamp(cast("int", row[1]), tz=UTC)) for row in rows]

    async def due_ids_before(self, moment: datetime, *, limit: int = 50) -> list[int]:
        """Return the ids of schedules due at or before ``moment``, oldest first."""
        stamp = to_epoch(moment)
        statement = (
            select(ScheduleRow.id)
            .where(ScheduleRow.is_paused == 0)
            .where(ScheduleRow.next_run_at_utc.is_not(None))
            .where(ScheduleRow.next_run_at_utc <= stamp)
            .order_by(ScheduleRow.next_run_at_utc, ScheduleRow.id)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def due_before(self, moment: datetime, *, limit: int = 50) -> list[Schedule]:
        stamp = to_epoch(moment)
        statement = (
            select(ScheduleRow)
            .where(ScheduleRow.is_paused == 0)
            .where(ScheduleRow.next_run_at_utc.is_not(None))
            .where(ScheduleRow.next_run_at_utc <= stamp)
            .order_by(ScheduleRow.next_run_at_utc)
            .limit(limit)
        )
        rows = (await self._session.scalars(statement)).all()
        return [_to_schedule(row) for row in rows]

    async def update_next_run(
        self,
        schedule_id: int,
        *,
        next_run_at: datetime | None,
        last_run_at: datetime | None,
        now: datetime,
    ) -> None:
        row = await self._session.get(ScheduleRow, schedule_id)
        if row is None:
            return
        row.next_run_at_utc = to_epoch(next_run_at)
        if last_run_at is not None:
            row.last_run_at_utc = to_epoch(last_run_at)
        row.updated_at = to_epoch(now)
        await self._session.flush()

    async def set_paused(
        self,
        schedule_id: int,
        *,
        paused: bool,
        next_run_at: datetime | None,
        now: datetime,
    ) -> Schedule | None:
        row = await self._session.get(ScheduleRow, schedule_id)
        if row is None:
            return None
        row.is_paused = 1 if paused else 0
        row.next_run_at_utc = to_epoch(next_run_at)
        row.updated_at = to_epoch(now)
        await self._session.flush()
        return _to_schedule(row)

    async def pause_for_poll(self, poll_id: int, *, now: datetime) -> tuple[int, ...]:
        """Pause every active schedule of ``poll_id`` and return their ids (FR-115)."""
        statement = (
            update(ScheduleRow)
            .where(ScheduleRow.poll_id == poll_id)
            .where(ScheduleRow.is_paused == 0)
            .values(is_paused=1, next_run_at_utc=None, updated_at=to_epoch(now))
            .returning(ScheduleRow.id)
        )
        return tuple((await self._session.scalars(statement)).all())

    async def delete(self, schedule_id: int) -> bool:
        row = await self._session.get(ScheduleRow, schedule_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def count(self, *, poll_id: int | None = None) -> int:
        """Count schedules, optionally for one poll."""
        statement = select(func.count()).select_from(ScheduleRow)
        if poll_id is not None:
            statement = statement.where(ScheduleRow.poll_id == poll_id)
        result = await self._session.scalar(statement)
        return int(result or 0)

    async def count_active(self) -> int:
        statement = (
            select(func.count())
            .select_from(ScheduleRow)
            .where(ScheduleRow.is_paused == 0)
            .where(ScheduleRow.next_run_at_utc.is_not(None))
        )
        result = await self._session.scalar(statement)
        return int(result or 0)
