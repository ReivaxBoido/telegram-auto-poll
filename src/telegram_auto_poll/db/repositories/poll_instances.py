"""Poll instance persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import PollInstance
from ...domain.enums import InstanceStatus, InstanceTrigger
from ..models import PollInstanceRow, PollResponseRow
from .common import from_epoch, to_epoch


def instance_from_row(row: PollInstanceRow) -> PollInstance:
    """Map a row to the domain entity."""
    return PollInstance(
        poll_id=row.poll_id,
        status=InstanceStatus(row.status),
        trigger=InstanceTrigger(row.trigger),
        scheduled_for=from_epoch(row.scheduled_for_utc),
        created_by=row.created_by,
        id=row.id,
        created_at=from_epoch(row.created_at),
        closed_at=from_epoch(row.closed_at),
    )


class PollInstanceRepository:
    """CRUD for poll instances."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, instance: PollInstance, *, now: datetime) -> PollInstance:
        """Insert one instance and return the persisted entity."""
        row = PollInstanceRow(
            poll_id=instance.poll_id,
            status=instance.status.value,
            trigger=instance.trigger.value,
            scheduled_for_utc=to_epoch(instance.scheduled_for),
            created_by=instance.created_by,
            created_at=to_epoch(now),
            closed_at=to_epoch(instance.closed_at),
        )
        self._session.add(row)
        await self._session.flush()
        return instance_from_row(row)

    async def get(self, instance_id: int) -> PollInstance | None:
        """Return one instance, or ``None``."""
        row = await self._session.get(PollInstanceRow, instance_id)
        return None if row is None else instance_from_row(row)

    async def latest_for_poll(self, poll_id: int) -> PollInstance | None:
        """Return the newest instance of a poll, or ``None``."""
        row = await self._session.scalar(
            select(PollInstanceRow)
            .where(PollInstanceRow.poll_id == poll_id)
            .order_by(PollInstanceRow.created_at.desc(), PollInstanceRow.id.desc())
            .limit(1)
        )
        return None if row is None else instance_from_row(row)

    async def latest_with_status(self, poll_id: int, status: InstanceStatus) -> PollInstance | None:
        """Return the newest instance of a poll in ``status``."""
        row = await self._session.scalar(
            select(PollInstanceRow)
            .where(PollInstanceRow.poll_id == poll_id, PollInstanceRow.status == status.value)
            .order_by(PollInstanceRow.created_at.desc(), PollInstanceRow.id.desc())
            .limit(1)
        )
        return None if row is None else instance_from_row(row)

    async def set_status(
        self,
        instance_id: int,
        status: InstanceStatus,
        *,
        now: datetime,
        closed_at: datetime | None = None,
    ) -> None:
        """Update an instance's lifecycle status."""
        await self._session.execute(
            update(PollInstanceRow)
            .where(PollInstanceRow.id == instance_id)
            .values(status=status.value, closed_at=to_epoch(closed_at))
        )

    async def list_for_poll(self, poll_id: int) -> list[PollInstance]:
        """Return every instance of a poll, newest first."""
        rows = (
            await self._session.scalars(
                select(PollInstanceRow)
                .where(PollInstanceRow.poll_id == poll_id)
                .order_by(PollInstanceRow.created_at.desc(), PollInstanceRow.id.desc())
            )
        ).all()
        return [instance_from_row(row) for row in rows]

    async def count_open(self) -> int:
        """Count open instances."""
        result = await self._session.scalar(
            select(func.count())
            .select_from(PollInstanceRow)
            .where(PollInstanceRow.status == InstanceStatus.OPEN.value)
        )
        return int(result or 0)

    async def list_expired_with_responses(
        self, cutoff: datetime, *, limit: int
    ) -> list[PollInstance]:
        """Return closed/deleted instances older than ``cutoff`` that still hold responses."""
        rows = (
            await self._session.scalars(
                select(PollInstanceRow)
                .where(
                    PollInstanceRow.status.in_(
                        [InstanceStatus.CLOSED.value, InstanceStatus.DELETED.value]
                    ),
                    func.coalesce(PollInstanceRow.closed_at, PollInstanceRow.created_at)
                    < to_epoch(cutoff),
                    select(PollResponseRow.id)
                    .where(PollResponseRow.instance_id == PollInstanceRow.id)
                    .exists(),
                )
                .order_by(PollInstanceRow.id)
                .limit(limit)
            )
        ).all()
        return [instance_from_row(row) for row in rows]
