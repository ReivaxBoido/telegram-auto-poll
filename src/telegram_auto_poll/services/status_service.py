"""Read-only status counters (SPEC-003 FR-338)."""

from __future__ import annotations

from dataclasses import dataclass

from ..db.uow import UnitOfWork
from ..domain.entities import PollMessage


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    """The counts shown by ``/status``; no names or user ids."""

    active_chats: int
    polls: int
    open_instances: int
    pending_messages: int
    active_responses: int
    recent_failures: tuple[PollMessage, ...]


class StatusService:
    """Collects the status counters in one read transaction."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def snapshot(self) -> StatusSnapshot:
        """Return the current counters."""
        async with self._uow() as repos:
            chats = await repos.chats.count(active_only=True)
            polls = await repos.polls.count()
            instances = await repos.instances.count_open()
            pending = await repos.messages.count_pending()
            responses = await repos.responses.count_all()
            failures = await repos.messages.list_recent_failures(limit=5)
        return StatusSnapshot(
            active_chats=chats,
            polls=polls,
            open_instances=instances,
            pending_messages=pending,
            active_responses=responses,
            recent_failures=tuple(failures),
        )
