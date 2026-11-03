"""Retention for responses, messages, and audit events (SPEC-003 FR-337).

Closed/deleted instances older than ``RESPONSE_RETENTION_DAYS`` are redacted first, then their
responses are deleted. The selection requires responses to still exist, so a second run is a no-op.
Message and audit rows older than ``RETENTION_DAYS`` are deleted in batches, then the WAL is
checkpointed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from ..db.uow import UnitOfWork
from ..domain.entities import BroadcastResult
from ..domain.ports import Clock

DEFAULT_RESPONSE_RETENTION_DAYS = 90
DEFAULT_RETENTION_DAYS = 90
DEFAULT_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """What one retention run removed."""

    cutoff: str
    responses: int
    messages: int
    audit_events: int
    batches: int


class RetentionService:
    """Prunes expired responses, messages, and audit rows."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        clock: Clock,
        checkpoint: Callable[[], Awaitable[None]],
        redact: Callable[[list[int]], Awaitable[BroadcastResult]],
        response_retention_days: int = DEFAULT_RESPONSE_RETENTION_DAYS,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._checkpoint = checkpoint
        self._redact = redact
        self._response_retention_days = response_retention_days
        self._retention_days = retention_days
        self._batch_size = batch_size

    async def run(self) -> RetentionResult:
        """Redact and prune expired data, then checkpoint the WAL."""
        now = self._clock.now()
        response_cutoff = now - timedelta(days=self._response_retention_days)
        audit_cutoff = now - timedelta(days=self._retention_days)
        responses = 0
        messages = 0
        audit_events = 0
        batches = 0

        while True:
            async with self._uow() as repos:
                expired = await repos.instances.list_expired_with_responses(
                    response_cutoff, limit=self._batch_size
                )
            if not expired:
                break
            for instance in expired:
                instance_id = instance.id or 0
                await self._redact([instance_id])
                async with self._uow() as repos:
                    responses += await repos.responses.delete_for_instance(instance_id)
                batches += 1
            if len(expired) < self._batch_size:
                break

        while True:
            async with self._uow() as repos:
                deleted = await repos.messages.delete_batch_before(
                    audit_cutoff, limit=self._batch_size
                )
            messages += deleted
            batches += 1
            if deleted < self._batch_size:
                break

        while True:
            async with self._uow() as repos:
                deleted = await repos.audit.delete_batch_before(
                    audit_cutoff, limit=self._batch_size
                )
            audit_events += deleted
            batches += 1
            if deleted < self._batch_size:
                break

        await self._checkpoint()
        return RetentionResult(
            cutoff=audit_cutoff.isoformat(),
            responses=responses,
            messages=messages,
            audit_events=audit_events,
            batches=batches,
        )
