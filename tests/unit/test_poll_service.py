"""Poll service behaviour (FR-116, FR-119, I-12)."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import AuditAction, ScheduleKind
from telegram_auto_poll.domain.errors import NotFoundError, ValidationError
from telegram_auto_poll.services.poll_service import PollService, ScheduleDraft
from tests.conftest import ADMIN_USER_ID, Seed
from tests.factories import FIXED_NOW, make_chat, make_poll
from tests.fakes import FakeClock

OPTIONS = ("Reliability", "Speed", "Docs")


def build(
    factory: async_sessionmaker[AsyncSession], clock: FakeClock | None = None
) -> tuple[PollService, FakeClock]:
    resolved = clock or FakeClock(FIXED_NOW)
    return PollService(uow=lambda: unit_of_work(factory), clock=resolved), resolved


async def test_create_persists_poll_targets_and_schedule_in_one_transaction(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)
    draft = ScheduleDraft(
        kind=ScheduleKind.WEEKLY,
        timezone="Europe/London",
        time_of_day=time(9, 30),
        weekdays=(1, 4),
        start_date=date(2026, 3, 2),
    )

    created = await service.create(
        actor_user_id=ADMIN_USER_ID,
        question="  What should we ship next?  ",
        options=OPTIONS,
        target_chat_ids=(seed.chat_id,),
        description="Pick one",
        schedule=draft,
    )

    assert created.poll.id is not None
    assert created.poll.question == "What should we ship next?"
    assert created.poll.options == OPTIONS
    assert created.poll.targets == (seed.chat_id,)
    assert created.schedule is not None
    assert created.schedule.poll_id == created.poll.id
    assert created.schedule.weekdays == (1, 4)

    async with unit_of_work(factory) as repos:
        stored = await repos.polls.get(created.poll.id)
        schedules = await repos.schedules.list_all(poll_id=created.poll.id)
        actions = [event.action for event in await repos.audit.list_recent(limit=10)]

    assert stored == created.poll
    assert [schedule.id for schedule in schedules] == [created.schedule.id]
    assert actions == [AuditAction.SCHEDULE_CREATE, AuditAction.POLL_CREATE]


async def test_create_without_a_schedule(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    created = await service.create(
        actor_user_id=ADMIN_USER_ID,
        question="Quick question",
        options=("Yes", "No"),
        target_chat_ids=(seed.chat_id,),
    )

    assert created.schedule is None
    async with unit_of_work(factory) as repos:
        assert await repos.schedules.count() == 1  # only the schedule created by the fixture
        assert [event.action for event in await repos.audit.list_recent()] == [
            AuditAction.POLL_CREATE
        ]


async def test_create_deduplicates_and_orders_targets(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        other = await repos.chats.upsert(
            make_chat(id=None, telegram_chat_id=-1_001_234_567_899), now=FIXED_NOW
        )
        assert other.id is not None

    service, _ = build(factory)
    created = await service.create(
        actor_user_id=ADMIN_USER_ID,
        question="Two chats",
        options=("A", "B"),
        target_chat_ids=(other.id, seed.chat_id, other.id),
    )

    assert created.poll.targets == tuple(sorted({seed.chat_id, other.id}))


async def test_create_requires_a_target(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    with pytest.raises(ValidationError) as exc_info:
        await service.create(
            actor_user_id=ADMIN_USER_ID,
            question="No targets",
            options=("A", "B"),
            target_chat_ids=(),
        )

    assert exc_info.value.field == "targets"
    async with unit_of_work(factory) as repos:
        assert await repos.polls.count() == 1  # only the poll created by the fixture


async def test_create_rejects_an_inactive_target(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.chats.set_active(seed.chat_id, active=False, now=FIXED_NOW)
        other = await repos.chats.upsert(
            make_chat(id=None, telegram_chat_id=-1_001_234_567_898), now=FIXED_NOW
        )
        assert other.id is not None
    service, _ = build(factory)

    with pytest.raises(ValidationError, match="inactive"):
        await service.create(
            actor_user_id=ADMIN_USER_ID,
            question="Inactive target",
            options=("A", "B"),
            target_chat_ids=(other.id, seed.chat_id),
        )


async def test_create_rejects_invalid_options(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    with pytest.raises(ValidationError):
        await service.create(
            actor_user_id=ADMIN_USER_ID,
            question="Only one option",
            options=("Solo",),
            target_chat_ids=(seed.chat_id,),
        )

    async with unit_of_work(factory) as repos:
        assert await repos.polls.count() == 1  # only the poll created by the fixture


async def test_a_failed_schedule_rolls_the_poll_back(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)
    past = FIXED_NOW - timedelta(minutes=1)
    draft = ScheduleDraft(kind=ScheduleKind.ONCE, timezone="UTC", run_at=past)

    with pytest.raises(ValidationError) as exc_info:
        await service.create(
            actor_user_id=ADMIN_USER_ID,
            question="Atomic",
            options=("A", "B"),
            target_chat_ids=(seed.chat_id,),
            schedule=draft,
        )

    assert exc_info.value.field == "run_at"
    async with unit_of_work(factory) as repos:
        assert await repos.polls.count() == 1  # the candidate poll was rolled back
        assert await repos.audit.list_recent() == []


async def test_page_paginates_poll_summaries(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        for index in range(8):
            await repos.polls.add(
                make_poll(id=None, question=f"Poll {index}", targets=()), now=FIXED_NOW
            )
    service, _ = build(factory)

    first = await service.page(1)
    second = await service.page(2)

    assert first.total == 9
    assert len(first.items) == 8
    assert first.pages == 2
    assert second.has_next is False
    assert len(second.items) == 1


async def test_page_can_include_archived_polls(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.polls.set_active(seed.poll_id, active=False, now=FIXED_NOW)
    service, _ = build(factory)

    active = await service.page(1)
    everything = await service.page(1, active_only=False)

    assert active.total == 0
    assert everything.total == 1


async def test_detail_reports_schedules_and_next_runs(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    detail = await service.detail(seed.poll_id)

    assert detail.poll.id == seed.poll_id
    assert len(detail.schedules) == 1
    assert len(detail.schedules[0].next_runs) == 5
    assert detail.schedules[0].next_runs == tuple(sorted(detail.schedules[0].next_runs))


async def test_detail_hides_runs_of_a_paused_schedule(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.schedules.set_paused(
            seed.schedule_id, paused=True, next_run_at=None, now=FIXED_NOW
        )
    service, _ = build(factory)

    detail = await service.detail(seed.poll_id)

    assert detail.schedules[0].next_runs == ()


async def test_detail_reports_a_missing_poll(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _ = build(factory)

    with pytest.raises(NotFoundError, match="does not exist"):
        await service.detail(404)


async def test_next_run_uses_the_injected_clock(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(FIXED_NOW)
    service, _ = build(factory, clock)

    first = await service.next_run(seed.schedule)
    assert first == FIXED_NOW.replace(hour=9, minute=0) + timedelta(days=1)

    clock.set(FIXED_NOW + timedelta(days=30))
    later = await service.next_run(seed.schedule)

    assert later is not None
    assert later > FIXED_NOW + timedelta(days=30)
