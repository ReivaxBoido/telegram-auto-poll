"""Unit of work: one session per service call, with every repository bound to it.

Services call ``uow()`` once per operation. The context manager commits on success and rolls back
on any exception, which is what makes "poll + targets + schedule + audit in one transaction"
(FR-116, FR-134) true at the storage level rather than by convention.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .engine import session_scope
from .repositories.audit import AuditRepository
from .repositories.chats import ChatRepository
from .repositories.deliveries import DeliveryRepository
from .repositories.polls import PollRepository
from .repositories.schedules import ScheduleRepository


@dataclass(frozen=True, slots=True)
class Repositories:
    """The repository bundle handed to a service for the duration of one transaction."""

    chats: ChatRepository
    polls: PollRepository
    schedules: ScheduleRepository
    deliveries: DeliveryRepository
    audit: AuditRepository


def repositories(session: AsyncSession) -> Repositories:
    """Bind every repository to ``session``."""
    return Repositories(
        chats=ChatRepository(session),
        polls=PollRepository(session),
        schedules=ScheduleRepository(session),
        deliveries=DeliveryRepository(session),
        audit=AuditRepository(session),
    )


@asynccontextmanager
async def unit_of_work(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[Repositories]:
    """Open one transaction and expose the repositories that share it."""
    async with session_scope(factory) as session:
        yield repositories(session)


#: The callable a service depends on: ``async with self._uow() as repos: ...``.
UnitOfWork = Callable[[], AbstractAsyncContextManager[Repositories]]
