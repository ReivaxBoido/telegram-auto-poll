"""Poll message persistence and worker claiming."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import PollMessage
from ...domain.enums import MessageStatus
from ..models import PollMessageRow
from .common import from_epoch, rowcount, to_epoch


def message_from_row(row: PollMessageRow) -> PollMessage:
    """Map a row to the domain entity."""
    return PollMessage(
        instance_id=row.instance_id,
        chat_id=row.chat_id,
        inline_message_id=row.inline_message_id,
        telegram_message_id=row.telegram_message_id,
        status=MessageStatus(row.status),
        attempt_count=row.attempt_count,
        next_attempt_at=from_epoch(row.next_attempt_at_utc),
        last_error=row.last_error,
        skip_reason=row.skip_reason,
        id=row.id,
        created_at=from_epoch(row.created_at),
        sent_at=from_epoch(row.sent_at),
        updated_at=from_epoch(row.updated_at),
    )


class PollMessageRepository:
    """CRUD and claim operations for poll messages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(
        self,
        instance_id: int,
        chat_ids: list[int],
        *,
        now: datetime,
    ) -> list[PollMessage]:
        """Create one pending message per chat."""
        rows = [
            PollMessageRow(
                instance_id=instance_id,
                chat_id=chat_id,
                status=MessageStatus.PENDING.value,
                attempt_count=0,
                created_at=to_epoch(now),
                updated_at=to_epoch(now),
            )
            for chat_id in chat_ids
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return [message_from_row(row) for row in rows]

    async def get(self, message_id: int) -> PollMessage | None:
        """Return one message, or ``None``."""
        row = await self._session.get(PollMessageRow, message_id)
        return None if row is None else message_from_row(row)

    async def list_for_instance(self, instance_id: int) -> list[PollMessage]:
        """Return every message of an instance, oldest first."""
        rows = (
            await self._session.scalars(
                select(PollMessageRow)
                .where(PollMessageRow.instance_id == instance_id)
                .order_by(PollMessageRow.id)
            )
        ).all()
        return [message_from_row(row) for row in rows]

    async def get_for_chat_message(
        self,
        *,
        instance_id: int,
        chat_id: int,
        telegram_message_id: int,
    ) -> PollMessage | None:
        """Return the message a callback points at, or ``None``."""
        row = await self._session.scalar(
            select(PollMessageRow).where(
                PollMessageRow.instance_id == instance_id,
                PollMessageRow.chat_id == chat_id,
                PollMessageRow.telegram_message_id == telegram_message_id,
            )
        )
        return None if row is None else message_from_row(row)

    async def get_for_inline(
        self,
        *,
        instance_id: int,
        inline_message_id: str,
    ) -> PollMessage | None:
        """Return the inline message a callback points at, or ``None``."""
        row = await self._session.scalar(
            select(PollMessageRow).where(
                PollMessageRow.instance_id == instance_id,
                PollMessageRow.inline_message_id == inline_message_id,
            )
        )
        return None if row is None else message_from_row(row)

    async def claim_pending(self, *, now: datetime, limit: int) -> list[PollMessage]:
        """Mark up to ``limit`` due messages as ``sending`` and return them."""
        rows = (
            await self._session.scalars(
                select(PollMessageRow)
                .where(
                    PollMessageRow.status == MessageStatus.PENDING.value,
                    (PollMessageRow.next_attempt_at_utc.is_(None))
                    | (PollMessageRow.next_attempt_at_utc <= to_epoch(now)),
                )
                .order_by(PollMessageRow.id)
                .limit(limit)
            )
        ).all()
        for row in rows:
            row.status = MessageStatus.SENDING.value
            row.attempt_count += 1
            row.updated_at = to_epoch(now)
        if rows:
            await self._session.flush()
        return [message_from_row(row) for row in rows]

    async def mark_sent(self, message_id: int, telegram_message_id: int, *, now: datetime) -> None:
        """Record a successful send."""
        await self._session.execute(
            update(PollMessageRow)
            .where(PollMessageRow.id == message_id)
            .values(
                status=MessageStatus.SENT.value,
                telegram_message_id=telegram_message_id,
                sent_at=to_epoch(now),
                updated_at=to_epoch(now),
                last_error=None,
                next_attempt_at_utc=None,
            )
        )

    async def mark_failed(self, message_id: int, error: str, *, now: datetime) -> None:
        """Record a terminal failure."""
        await self._session.execute(
            update(PollMessageRow)
            .where(PollMessageRow.id == message_id)
            .values(
                status=MessageStatus.FAILED.value,
                last_error=error,
                next_attempt_at_utc=None,
                updated_at=to_epoch(now),
            )
        )

    async def mark_skipped(self, message_id: int, reason: str, *, now: datetime) -> None:
        """Record a skip without sending."""
        await self._session.execute(
            update(PollMessageRow)
            .where(PollMessageRow.id == message_id)
            .values(
                status=MessageStatus.SKIPPED.value,
                skip_reason=reason,
                next_attempt_at_utc=None,
                updated_at=to_epoch(now),
            )
        )

    async def mark_missing(self, message_id: int, *, now: datetime) -> None:
        """Record that Telegram no longer has the message."""
        await self._session.execute(
            update(PollMessageRow)
            .where(PollMessageRow.id == message_id)
            .values(
                status=MessageStatus.MISSING.value,
                last_error="message_not_found",
                next_attempt_at_utc=None,
                updated_at=to_epoch(now),
            )
        )

    async def reset_for_retry(
        self, message_id: int, *, next_attempt_at: datetime, now: datetime
    ) -> None:
        """Return a failed message to ``pending`` with a future attempt."""
        await self._session.execute(
            update(PollMessageRow)
            .where(PollMessageRow.id == message_id)
            .values(
                status=MessageStatus.PENDING.value,
                next_attempt_at_utc=to_epoch(next_attempt_at),
                updated_at=to_epoch(now),
            )
        )

    async def fail_stale_sending(self, reason: str, *, now: datetime) -> int:
        """Fail every row left in ``sending`` by a crash."""
        result = await self._session.execute(
            update(PollMessageRow)
            .where(PollMessageRow.status == MessageStatus.SENDING.value)
            .values(
                status=MessageStatus.FAILED.value,
                last_error=reason,
                next_attempt_at_utc=None,
                updated_at=to_epoch(now),
            )
        )
        return rowcount(result)

    async def skip_pending_for_instance(
        self, instance_id: int, reason: str, *, now: datetime
    ) -> int:
        """Skip every pending message of an instance."""
        result = await self._session.execute(
            update(PollMessageRow)
            .where(
                PollMessageRow.instance_id == instance_id,
                PollMessageRow.status == MessageStatus.PENDING.value,
            )
            .values(
                status=MessageStatus.SKIPPED.value,
                skip_reason=reason,
                updated_at=to_epoch(now),
            )
        )
        return rowcount(result)

    async def skip_pending_for_chat(self, chat_id: int, reason: str, *, now: datetime) -> int:
        """Skip every pending message for a deactivated chat."""
        result = await self._session.execute(
            update(PollMessageRow)
            .where(
                PollMessageRow.chat_id == chat_id,
                PollMessageRow.status == MessageStatus.PENDING.value,
            )
            .values(
                status=MessageStatus.SKIPPED.value,
                skip_reason=reason,
                updated_at=to_epoch(now),
            )
        )
        return rowcount(result)

    async def reset_failed_for_instance(self, instance_id: int, *, now: datetime) -> int:
        """Return every failed message of an instance to ``pending`` for a refresh."""
        result = await self._session.execute(
            update(PollMessageRow)
            .where(
                PollMessageRow.instance_id == instance_id,
                PollMessageRow.status == MessageStatus.FAILED.value,
            )
            .values(
                status=MessageStatus.PENDING.value,
                next_attempt_at_utc=to_epoch(now),
                updated_at=to_epoch(now),
            )
        )
        return rowcount(result)

    async def counts(self) -> dict[MessageStatus, int]:
        """Count messages by status."""
        rows = await self._session.execute(
            select(PollMessageRow.status, func.count()).group_by(PollMessageRow.status)
        )
        return {MessageStatus(status): int(count) for status, count in rows.all()}

    async def count_pending(self) -> int:
        """Count pending messages."""
        result = await self._session.scalar(
            select(func.count())
            .select_from(PollMessageRow)
            .where(PollMessageRow.status == MessageStatus.PENDING.value)
        )
        return int(result or 0)

    async def list_recent_failures(self, *, limit: int) -> list[PollMessage]:
        """Return the newest failed messages."""
        rows = (
            await self._session.scalars(
                select(PollMessageRow)
                .where(PollMessageRow.status == MessageStatus.FAILED.value)
                .order_by(PollMessageRow.updated_at.desc(), PollMessageRow.id.desc())
                .limit(limit)
            )
        ).all()
        return [message_from_row(row) for row in rows]

    async def delete_batch_before(self, cutoff: datetime, *, limit: int) -> int:
        """Delete up to ``limit`` messages older than ``cutoff``."""
        ids = (
            await self._session.scalars(
                select(PollMessageRow.id)
                .where(PollMessageRow.created_at < to_epoch(cutoff))
                .limit(limit)
            )
        ).all()
        if not ids:
            return 0
        await self._session.execute(delete(PollMessageRow).where(PollMessageRow.id.in_(ids)))
        return len(ids)
