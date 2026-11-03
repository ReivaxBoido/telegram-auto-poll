"""Poll lifecycle services (SPEC-003 FR-306 - FR-316)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.poll_messages import PollMessageRepository
from telegram_auto_poll.db.repositories.poll_responses import PollResponseRepository
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.entities import BroadcastResult
from telegram_auto_poll.domain.enums import InstanceStatus, PollStatus
from telegram_auto_poll.domain.errors import NotFoundError, ValidationError
from telegram_auto_poll.services.poll_service import PollService
from tests.conftest import Seed
from tests.factories import FIXED_NOW
from tests.fakes import FakeClock


@dataclass
class FakeBroadcaster:
    """Records render/redact calls without touching Telegram."""

    refreshed: list[int] = field(default_factory=list)
    redacted: list[int] = field(default_factory=list)
    dirty: list[int] = field(default_factory=list)

    def mark_dirty(self, instance_id: int) -> None:
        self.dirty.append(instance_id)

    async def refresh(self, instance_id: int) -> BroadcastResult:
        self.refreshed.append(instance_id)
        return BroadcastResult(edited=1)

    async def redact(self, instance_ids: list[int]) -> BroadcastResult:
        self.redacted.extend(instance_ids)
        return BroadcastResult(edited=len(instance_ids))


@pytest.fixture
def broadcaster() -> FakeBroadcaster:
    return FakeBroadcaster()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(FIXED_NOW)


@pytest.fixture
def service(
    factory: async_sessionmaker[AsyncSession], clock: FakeClock, broadcaster: FakeBroadcaster
) -> PollService:
    return PollService(
        uow=lambda: unit_of_work(factory),
        clock=clock,
        broadcaster=broadcaster,  # type: ignore[arg-type]
    )


async def test_create_persists_a_draft_and_audits(
    service: PollService, factory: async_sessionmaker[AsyncSession]
) -> None:
    created = await service.create(
        actor_user_id=100_000_001,
        title="  Game  ",
        options=["In", "Out"],
    )

    assert created.id is not None
    assert created.status is PollStatus.DRAFT
    async with session_scope(factory) as session:
        from sqlalchemy import select

        from telegram_auto_poll.db.models import AuditEventRow

        audit = (await session.scalars(select(AuditEventRow))).all()
    assert audit[0].action == "poll.create"


async def test_page_and_detail_expose_counts(
    service: PollService,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await PollResponseRepository(session).toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            now=FIXED_NOW,
        )

    page = await service.page(1)
    detail = await service.detail(seed.poll_id)

    assert page.total == 1
    assert page.items[0].response_count == 1
    assert detail.total_respondents == 1
    assert detail.counts[seed.poll.options[0].id or 0] == 1


async def test_detail_unknown_poll_raises(service: PollService) -> None:
    with pytest.raises(NotFoundError):
        await service.detail(999)


async def test_close_reopen_and_refresh(
    service: PollService,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    broadcaster: FakeBroadcaster,
) -> None:
    closed = await service.close(seed.poll_id, actor_user_id=1)
    assert closed.status is InstanceStatus.CLOSED
    assert broadcaster.refreshed == [seed.instance_id]

    reopened = await service.reopen(seed.poll_id, actor_user_id=1)
    assert reopened.status is InstanceStatus.OPEN

    async with session_scope(factory) as session:
        await PollMessageRepository(session).mark_failed(
            seed.message_id, "bad_request", now=FIXED_NOW
        )
    refreshed = await service.refresh(seed.poll_id, actor_user_id=1)
    assert refreshed.retried == 1
    assert refreshed.broadcast.edited == 1


async def test_close_without_open_instance_raises(service: PollService, seed: Seed) -> None:
    await service.close(seed.poll_id, actor_user_id=1)
    with pytest.raises(NotFoundError):
        await service.close(seed.poll_id, actor_user_id=1)


async def test_reopen_without_closed_instance_raises(service: PollService, seed: Seed) -> None:
    with pytest.raises(NotFoundError):
        await service.reopen(seed.poll_id, actor_user_id=1)


async def test_delete_removes_responses_and_redacts(
    service: PollService,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    broadcaster: FakeBroadcaster,
) -> None:
    async with session_scope(factory) as session:
        await PollResponseRepository(session).toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            now=FIXED_NOW,
        )

    result = await service.delete(seed.poll_id, actor_user_id=1)

    assert result.edited == 1
    assert broadcaster.redacted == [seed.instance_id]
    with pytest.raises(NotFoundError):
        await service.detail(seed.poll_id)


async def test_responses_paginates_and_validates(
    service: PollService,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        repo = PollResponseRepository(session)
        for index in range(45):
            await repo.toggle(
                instance_id=seed.instance_id,
                option_id=seed.poll.options[0].id or 0,
                telegram_user_id=index + 1,
                first_name=f"Name {index}",
                now=FIXED_NOW,
            )

    first = await service.responses(seed.poll_id, option_position=1, page=1)
    third = await service.responses(seed.poll_id, option_position=1, page=3)

    assert len(first.names) == 20
    assert third.pages == 3
    assert len(third.names) == 5
    with pytest.raises(ValidationError, match="option must be between"):
        await service.responses(seed.poll_id, option_position=9)
    with pytest.raises(ValidationError, match="page must be between"):
        await service.responses(seed.poll_id, option_position=1, page=9)


async def test_responses_requires_a_published_poll(
    service: PollService,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await service.create(actor_user_id=1, title="Draft", options=["In"])
    with pytest.raises(NotFoundError):
        await service.responses(created.id or 0)
