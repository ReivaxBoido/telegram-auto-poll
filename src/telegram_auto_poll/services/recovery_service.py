"""Startup recovery (FR-104, FR-128).

A crash can leave two kinds of ambiguity: a row stuck in ``sending`` (the Bot API call may or may
not have landed) and an occurrence that came due while the process was down. The first is failed
without retry — the constitution's at-most-once rule wins over completeness — and the second is
resolved by the normal planning pass, which sends inside the grace window and records a skip
outside it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..db.uow import UnitOfWork
from ..domain.ports import AdminNotifier, Clock
from .delivery_service import TickOutcome

AMBIGUOUS_OUTCOME_REASON = "ambiguous_outcome_after_restart"


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """What startup recovery changed."""

    failed_deliveries: int
    planned_occurrences: int
    created_deliveries: int
    skipped_deliveries: int


class RecoveryService:
    """Fails ambiguous rows, then lets planning resolve overdue occurrences."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        clock: Clock,
        notifier: AdminNotifier,
        plan: Callable[[], Awaitable[TickOutcome]],
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._notifier = notifier
        self._plan = plan

    async def run(self) -> RecoveryResult:
        """Recover the database after an unclean shutdown."""
        now = self._clock.now()
        async with self._uow() as repos:
            failed = await repos.deliveries.fail_stale_sending(
                reason=AMBIGUOUS_OUTCOME_REASON, now=now
            )

        tick = await self._plan()

        if failed:
            await self._notifier.notify_admins(
                f"startup recovery failed {failed} delivery row(s) with an unknown outcome "
                f"({AMBIGUOUS_OUTCOME_REASON}); they will not be retried, so check the target "
                "chats for a duplicate poll before resending"
            )

        return RecoveryResult(
            failed_deliveries=failed,
            planned_occurrences=tick.schedules,
            created_deliveries=tick.created,
            skipped_deliveries=tick.skipped,
        )
