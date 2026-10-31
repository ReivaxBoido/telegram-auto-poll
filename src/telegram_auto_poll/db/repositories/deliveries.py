"""Delivery repository: idempotent creation, claiming, and terminal transitions.

Nothing here commits. The caller owns the transaction (``db.engine.session_scope``); a repository
method must be safe to call twice inside one unit of work without duplicating a row (FR-127).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import Delivery
from ...domain.enums import DeliveryStatus, Trigger
from ..models import DeliveryRow
from .common import from_epoch, rowcount, to_epoch

#: The partial unique index that makes scheduled delivery creation idempotent (I-9, FR-127).
CONFLICT_COLUMNS = ("schedule_id", "scheduled_for_utc", "chat_id")


def _to_delivery(row: DeliveryRow) -> Delivery:
    return Delivery(
        poll_id=row.poll_id,
        chat_id=row.chat_id,
        trigger=Trigger(row.trigger),
        status=DeliveryStatus(row.status),
        scheduled_for=from_epoch(row.scheduled_for_utc),
        schedule_id=row.schedule_id,
        attempt_count=row.attempt_count,
        next_attempt_at=from_epoch(row.next_attempt_at_utc),
        telegram_message_id=row.telegram_message_id,
        last_error=row.last_error,
        skip_reason=row.skip_reason,
        started_at=from_epoch(row.started_at),
        completed_at=from_epoch(row.completed_at),
        id=row.id,
        created_at=from_epoch(row.created_at),
    )


def _insert_values(delivery: Delivery, now: datetime) -> dict[str, object]:
    if delivery.scheduled_for is None:
        raise ValueError("a delivery requires scheduled_for")
    return {
        "poll_id": delivery.poll_id,
        "chat_id": delivery.chat_id,
        "schedule_id": delivery.schedule_id,
        "trigger": delivery.trigger.value,
        "scheduled_for_utc": to_epoch(delivery.scheduled_for),
        "status": delivery.status.value,
        "attempt_count": delivery.attempt_count,
        "next_attempt_at_utc": to_epoch(delivery.next_attempt_at),
        "telegram_message_id": delivery.telegram_message_id,
        "last_error": delivery.last_error,
        "skip_reason": delivery.skip_reason,
        "created_at": to_epoch(delivery.created_at or now),
        "started_at": to_epoch(delivery.started_at),
        "completed_at": to_epoch(delivery.completed_at),
    }


class DeliveryRepository:
    """Reads and writes ``deliveries``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_many_ignore(self, deliveries: Sequence[Delivery], *, now: datetime) -> int:
        """Insert rows, ignoring scheduled duplicates; return the number actually inserted.

        Scheduled rows go through ``ON CONFLICT DO NOTHING`` against the partial unique index, so a
        repeated tick for the same occurrence is a no-op rather than an error (FR-127). Manual rows
        have a NULL ``schedule_id``, are not covered by that index, and are inserted plainly so
        ``/sendnow`` may duplicate a scheduled occurrence (FR-132).
        """
        if not deliveries:
            return 0
        scheduled = [d for d in deliveries if d.schedule_id is not None]
        manual = [d for d in deliveries if d.schedule_id is None]
        inserted = 0
        if scheduled:
            statement = (
                sqlite_insert(DeliveryRow)
                .values([_insert_values(delivery, now) for delivery in scheduled])
                .on_conflict_do_nothing(
                    index_elements=list(CONFLICT_COLUMNS),
                    index_where=DeliveryRow.__table__.c.schedule_id.is_not(None),
                )
            )
            result = await self._session.execute(statement)
            inserted += rowcount(result)
        if manual:
            statement = sqlite_insert(DeliveryRow).values(
                [_insert_values(delivery, now) for delivery in manual]
            )
            result = await self._session.execute(statement)
            inserted += rowcount(result)
        await self._session.flush()
        return inserted

    async def get(self, delivery_id: int) -> Delivery | None:
        row = await self._session.get(DeliveryRow, delivery_id)
        return None if row is None else _to_delivery(row)

    async def list_for_occurrence(
        self, schedule_id: int, scheduled_for: datetime
    ) -> list[Delivery]:
        """Return every row for one scheduled occurrence, ordered by chat id."""
        statement = (
            select(DeliveryRow)
            .where(DeliveryRow.schedule_id == schedule_id)
            .where(DeliveryRow.scheduled_for_utc == to_epoch(scheduled_for))
            .order_by(DeliveryRow.chat_id)
        )
        rows = (await self._session.scalars(statement)).all()
        return [_to_delivery(row) for row in rows]

    async def claim_due(self, moment: datetime, *, limit: int) -> list[Delivery]:
        """Move up to ``limit`` due ``pending`` rows to ``sending`` and return them.

        Claiming increments ``attempt_count`` and stamps ``started_at`` in the same statement that
        flips the status, so two workers started against one database cannot claim the same row.
        """
        stamp = to_epoch(moment)
        due_ids = (
            select(DeliveryRow.id)
            .where(DeliveryRow.status == DeliveryStatus.PENDING.value)
            .where(
                or_(
                    DeliveryRow.next_attempt_at_utc.is_(None),
                    DeliveryRow.next_attempt_at_utc <= stamp,
                )
            )
            .order_by(func.coalesce(DeliveryRow.next_attempt_at_utc, 0), DeliveryRow.id)
            .limit(limit)
        )
        statement = (
            update(DeliveryRow)
            .where(DeliveryRow.id.in_(due_ids))
            .where(DeliveryRow.status == DeliveryStatus.PENDING.value)
            .values(
                status=DeliveryStatus.SENDING.value,
                attempt_count=DeliveryRow.attempt_count + 1,
                started_at=stamp,
            )
            .returning(DeliveryRow)
        )
        rows = (await self._session.scalars(statement)).all()
        return [_to_delivery(row) for row in rows]

    async def _apply(self, delivery_id: int, values: dict[str, object]) -> Delivery | None:
        statement = (
            update(DeliveryRow)
            .where(DeliveryRow.id == delivery_id)
            .values(**values)
            .returning(DeliveryRow)
        )
        row = (await self._session.scalars(statement)).one_or_none()
        return None if row is None else _to_delivery(row)

    async def mark_sent(
        self, delivery_id: int, *, telegram_message_id: int, now: datetime
    ) -> Delivery | None:
        """Record success (I-10): message id and completion instant are always set together."""
        return await self._apply(
            delivery_id,
            {
                "status": DeliveryStatus.SENT.value,
                "telegram_message_id": telegram_message_id,
                "completed_at": to_epoch(now),
                "next_attempt_at_utc": None,
                "last_error": None,
            },
        )

    async def mark_retry(
        self, delivery_id: int, *, next_attempt_at: datetime, error: str, now: datetime
    ) -> Delivery | None:
        """Return the row to ``pending`` with a bounded-backoff instant (FR-131)."""
        return await self._apply(
            delivery_id,
            {
                "status": DeliveryStatus.PENDING.value,
                "next_attempt_at_utc": to_epoch(next_attempt_at),
                "last_error": error,
            },
        )

    async def mark_failed(self, delivery_id: int, *, error: str, now: datetime) -> Delivery | None:
        """Record a terminal failure; ``completed_at`` is required for terminal rows (I-11)."""
        return await self._apply(
            delivery_id,
            {
                "status": DeliveryStatus.FAILED.value,
                "completed_at": to_epoch(now),
                "next_attempt_at_utc": None,
                "last_error": error,
            },
        )

    async def mark_skipped(
        self, delivery_id: int, *, reason: str, now: datetime
    ) -> Delivery | None:
        """Record a skip with its reason, without ever calling Telegram (FR-128)."""
        return await self._apply(
            delivery_id,
            {
                "status": DeliveryStatus.SKIPPED.value,
                "skip_reason": reason,
                "completed_at": to_epoch(now),
                "next_attempt_at_utc": None,
            },
        )

    async def skip_pending_for_chat(self, chat_id: int, *, reason: str, now: datetime) -> int:
        """Skip every pending row for an deactivated chat (FR-115)."""
        statement = (
            update(DeliveryRow)
            .where(DeliveryRow.chat_id == chat_id)
            .where(DeliveryRow.status == DeliveryStatus.PENDING.value)
            .values(
                status=DeliveryStatus.SKIPPED.value,
                skip_reason=reason,
                completed_at=to_epoch(now),
                next_attempt_at_utc=None,
            )
        )
        result = await self._session.execute(statement)
        return rowcount(result)

    async def fail_stale_sending(self, *, reason: str, now: datetime) -> int:
        """Fail rows left in ``sending`` by a crash (delivery is at-most-once, never retried)."""
        statement = (
            update(DeliveryRow)
            .where(DeliveryRow.status == DeliveryStatus.SENDING.value)
            .values(
                status=DeliveryStatus.FAILED.value,
                completed_at=to_epoch(now),
                next_attempt_at_utc=None,
                last_error=reason,
            )
        )
        result = await self._session.execute(statement)
        return rowcount(result)

    async def counts(self) -> dict[DeliveryStatus, int]:
        """Return a count per status, including statuses with no rows, for ``/status``."""
        statement = select(DeliveryRow.status, func.count()).group_by(DeliveryRow.status)
        rows = (await self._session.execute(statement)).all()
        totals: dict[DeliveryStatus, int] = dict.fromkeys(DeliveryStatus, 0)
        for raw_status, count in rows:
            totals[DeliveryStatus(raw_status)] = int(count)
        return totals

    async def list_recent(self, *, limit: int = 10) -> list[Delivery]:
        """Return the newest deliveries by id, across every status."""
        statement = select(DeliveryRow).order_by(DeliveryRow.id.desc()).limit(limit)
        rows = (await self._session.scalars(statement)).all()
        return [_to_delivery(row) for row in rows]

    async def list_recent_failures(self, *, limit: int = 5) -> list[Delivery]:
        """Return the newest terminal failures, for ``/status`` and admin alerts."""
        statement = (
            select(DeliveryRow)
            .where(DeliveryRow.status == DeliveryStatus.FAILED.value)
            .order_by(DeliveryRow.completed_at.desc(), DeliveryRow.id.desc())
            .limit(limit)
        )
        rows = (await self._session.scalars(statement)).all()
        return [_to_delivery(row) for row in rows]

    async def delete_batch_before(self, cutoff: datetime, *, limit: int) -> int:
        """Delete at most ``limit`` rows scheduled before ``cutoff`` (FR-135 batching)."""
        stamp = to_epoch(cutoff)
        stale_ids = (
            select(DeliveryRow.id)
            .where(DeliveryRow.scheduled_for_utc < stamp)
            .order_by(DeliveryRow.id)
            .limit(limit)
        )
        statement = delete(DeliveryRow).where(DeliveryRow.id.in_(stale_ids))
        result = await self._session.execute(statement)
        return rowcount(result)
