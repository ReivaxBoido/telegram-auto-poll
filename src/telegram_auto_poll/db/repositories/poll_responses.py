"""Poll response persistence and toggling."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import PollResponse
from ..models import PollResponseRow
from .common import from_epoch, rowcount, to_epoch


def response_from_row(row: PollResponseRow) -> PollResponse:
    """Map a row to the domain entity."""
    return PollResponse(
        instance_id=row.instance_id,
        option_id=row.option_id,
        telegram_user_id=row.telegram_user_id,
        first_name=row.first_name,
        id=row.id,
        created_at=from_epoch(row.created_at),
        updated_at=from_epoch(row.updated_at),
    )


class PollResponseRepository:
    """CRUD for participant responses."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def toggle(
        self,
        *,
        instance_id: int,
        option_id: int,
        telegram_user_id: int,
        first_name: str,
        now: datetime,
    ) -> bool:
        """Add or remove one response. Returns ``True`` when it was added."""
        existing = await self._session.scalar(
            select(PollResponseRow).where(
                PollResponseRow.instance_id == instance_id,
                PollResponseRow.option_id == option_id,
                PollResponseRow.telegram_user_id == telegram_user_id,
            )
        )
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()
            return False

        self._session.add(
            PollResponseRow(
                instance_id=instance_id,
                option_id=option_id,
                telegram_user_id=telegram_user_id,
                first_name=first_name,
                created_at=to_epoch(now),
                updated_at=to_epoch(now),
            )
        )
        await self._session.flush()
        return True

    async def list_for_instance(self, instance_id: int) -> list[PollResponse]:
        """Return every response of an instance in stable order."""
        rows = (
            await self._session.scalars(
                select(PollResponseRow)
                .where(PollResponseRow.instance_id == instance_id)
                .order_by(
                    PollResponseRow.option_id,
                    PollResponseRow.created_at,
                    PollResponseRow.id,
                )
            )
        ).all()
        return [response_from_row(row) for row in rows]

    async def count_for_instance(self, instance_id: int) -> int:
        """Count distinct respondents in an instance."""
        result = await self._session.scalar(
            select(func.count(func.distinct(PollResponseRow.telegram_user_id))).where(
                PollResponseRow.instance_id == instance_id
            )
        )
        return int(result or 0)

    async def counts_by_option(self, instance_id: int) -> dict[int, int]:
        """Count responses per option."""
        rows = await self._session.execute(
            select(PollResponseRow.option_id, func.count())
            .where(PollResponseRow.instance_id == instance_id)
            .group_by(PollResponseRow.option_id)
        )
        return {option_id: int(count) for option_id, count in rows.all()}

    async def delete_for_instance(self, instance_id: int) -> int:
        """Delete every response of an instance."""
        result = await self._session.execute(
            delete(PollResponseRow).where(PollResponseRow.instance_id == instance_id)
        )
        return rowcount(result)

    async def delete_for_instances(self, instance_ids: list[int]) -> int:
        """Delete every response of the listed instances."""
        if not instance_ids:
            return 0
        result = await self._session.execute(
            delete(PollResponseRow).where(PollResponseRow.instance_id.in_(instance_ids))
        )
        return rowcount(result)

    async def count_all(self) -> int:
        """Count all current responses."""
        result = await self._session.scalar(select(func.count()).select_from(PollResponseRow))
        return int(result or 0)
