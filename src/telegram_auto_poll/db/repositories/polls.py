"""Poll definition persistence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import Poll, PollOption
from ...domain.enums import PollStatus
from ..models import PollOptionRow, PollRow
from .common import to_epoch


def _option_from_row(row: PollOptionRow) -> PollOption:
    return PollOption(
        label=row.label,
        position=row.position,
        poll_id=row.poll_id,
        id=row.id,
    )


def poll_from_rows(poll_row: PollRow, option_rows: list[PollOptionRow]) -> Poll:
    """Map a poll row and its ordered options to the domain entity."""
    ordered = sorted(option_rows, key=lambda row: row.position)
    return Poll(
        title=poll_row.title,
        options=tuple(_option_from_row(row) for row in ordered),
        status=PollStatus(poll_row.status),
        created_by=poll_row.created_by,
        id=poll_row.id,
        created_at=datetime.fromtimestamp(poll_row.created_at, tz=UTC),
        updated_at=datetime.fromtimestamp(poll_row.updated_at, tz=UTC),
    )


class PollRepository:
    """CRUD for polls and their options."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, poll: Poll, *, now: datetime) -> Poll:
        """Insert a poll and its options, returning the persisted entity."""
        row = PollRow(
            title=poll.title,
            status=poll.status.value,
            created_by=poll.created_by,
            created_at=to_epoch(now),
            updated_at=to_epoch(now),
        )
        self._session.add(row)
        await self._session.flush()

        option_rows = [
            PollOptionRow(
                poll_id=row.id,
                position=option.position,
                label=option.label,
                label_folded=option.label.casefold(),
                created_at=to_epoch(now),
            )
            for option in poll.options
        ]
        self._session.add_all(option_rows)
        await self._session.flush()
        return poll_from_rows(row, option_rows)

    async def get(self, poll_id: int) -> Poll | None:
        """Return one poll with its options, or ``None``."""
        row = await self._session.get(PollRow, poll_id)
        if row is None:
            return None
        options = await self._options_for([poll_id])
        return poll_from_rows(row, options.get(poll_id, []))

    async def list_all(self, *, limit: int, offset: int) -> list[Poll]:
        """Return non-deleted polls, newest first."""
        rows = (
            await self._session.scalars(
                select(PollRow)
                .where(PollRow.status != PollStatus.DELETED.value)
                .order_by(PollRow.created_at.desc(), PollRow.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        if not rows:
            return []
        options = await self._options_for([row.id for row in rows])
        return [poll_from_rows(row, options.get(row.id, [])) for row in rows]

    async def count(self) -> int:
        """Count non-deleted polls."""
        result = await self._session.scalar(
            select(func.count())
            .select_from(PollRow)
            .where(PollRow.status != PollStatus.DELETED.value)
        )
        return int(result or 0)

    async def set_status(self, poll_id: int, status: PollStatus, *, now: datetime) -> None:
        """Update a poll's lifecycle status."""
        await self._session.execute(
            update(PollRow)
            .where(PollRow.id == poll_id)
            .values(status=status.value, updated_at=to_epoch(now))
        )

    async def _options_for(self, poll_ids: list[int]) -> dict[int, list[PollOptionRow]]:
        if not poll_ids:
            return {}
        rows = (
            await self._session.scalars(
                select(PollOptionRow)
                .where(PollOptionRow.poll_id.in_(poll_ids))
                .order_by(PollOptionRow.poll_id, PollOptionRow.position)
            )
        ).all()
        grouped: dict[int, list[PollOptionRow]] = {}
        for row in rows:
            grouped.setdefault(row.poll_id, []).append(row)
        return grouped
