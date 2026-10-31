"""Retention (FR-135): delete old deliveries and audit events in fixed-size batches, then
checkpoint the WAL.

Batching keeps each transaction short, so the scheduler and the bot keep writing while retention
runs. The job is idempotent: a second run at the same cutoff deletes nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from ..db.uow import UnitOfWork
from ..domain.ports import Clock

DEFAULT_RETENTION_DAYS = 90
DEFAULT_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """What one retention run removed."""

    cutoff: str
    deliveries: int
    audit_events: int
    batches: int


class RetentionService:
    """Deletes rows older than ``RETENTION_DAYS`` and checkpoints the WAL."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        clock: Clock,
        checkpoint: Callable[[], Awaitable[None]],
        retention_days: int = DEFAULT_RETENTION_DAYS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._checkpoint = checkpoint
        self._retention_days = retention_days
        self._batch_size = batch_size

    async def run(self) -> RetentionResult:
        """Delete every row older than the cutoff, batch by batch, then checkpoint."""
        cutoff = self._clock.now() - timedelta(days=self._retention_days)
        deliveries = 0
        audit_events = 0
        batches = 0

        while True:
            async with self._uow() as repos:
                deleted = await repos.deliveries.delete_batch_before(cutoff, limit=self._batch_size)
            deliveries += deleted
            batches += 1
            if deleted < self._batch_size:
                break

        while True:
            async with self._uow() as repos:
                deleted = await repos.audit.delete_batch_before(cutoff, limit=self._batch_size)
            audit_events += deleted
            batches += 1
            if deleted < self._batch_size:
                break

        await self._checkpoint()
        return RetentionResult(
            cutoff=cutoff.isoformat(),
            deliveries=deliveries,
            audit_events=audit_events,
            batches=batches,
        )
