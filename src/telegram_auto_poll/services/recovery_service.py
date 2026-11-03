"""Startup recovery (SPEC-003 FR-303).

A crash can leave a message stuck in ``sending``: the Bot API call may or may not have landed. The
row is failed without retry; the at-most-once rule wins over completeness. Pending rows stay
claimable, and sent messages are left untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db.uow import UnitOfWork
from ..domain.ports import AdminNotifier, Clock
from .posting_worker import AMBIGUOUS_OUTCOME_REASON


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """What startup recovery changed."""

    failed_messages: int


class RecoveryService:
    """Fails ambiguous rows and alerts admins once."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        clock: Clock,
        notifier: AdminNotifier,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._notifier = notifier

    async def run(self) -> RecoveryResult:
        """Recover the database after an unclean shutdown."""
        async with self._uow() as repos:
            failed = await repos.messages.fail_stale_sending(
                AMBIGUOUS_OUTCOME_REASON, now=self._clock.now()
            )
        if failed:
            await self._notifier.notify_admins(
                f"startup recovery failed {failed} poll message(s) with an unknown outcome "
                f"({AMBIGUOUS_OUTCOME_REASON}); they will not be retried"
            )
        return RecoveryResult(failed_messages=failed)
