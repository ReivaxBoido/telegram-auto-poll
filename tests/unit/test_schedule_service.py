"""Schedule service behaviour (FR-121 - FR-124)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import AuditAction, ScheduleKind
from telegram_auto_poll.domain.errors import ConflictError, NotFoundError, ValidationError
from telegram_auto_poll.services.poll_service import ScheduleDraft
from telegram_auto_poll.services.schedule_service import ScheduleService
from tests.conftest import ADMIN_USER_ID, Seed
from tests.factories import FIXED_NOW, make_schedule
from tests.fakes import FakeClock


def build(
    factory: async_sessionmaker[AsyncSession], clock: FakeClock | None = None
) -> tuple[ScheduleService, FakeClock]:
    resolved = clock or FakeClock(FIXED_NOW)
    return ScheduleService(uow=lambda: unit_of_work(factory), clock=resolved), resolved


def daily_draft(**overrides: object) -> ScheduleDraft:
    defaults: dict[str, object] = {
        "kind": ScheduleKind.DAILY,
        "timezone": "UTC",
        "time_of_day": time(9, 0),
    }
    return ScheduleDraft(**{**defaults, **overrides})  # type: ignore[arg-type]


async def test_create_computes_the_next_run_and_audits(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    schedule = await service.create(
        poll_id=seed.poll_id, actor_user_id=ADMIN_USER_ID, draft=daily_draft()
    )

    assert schedule.id is not None
    assert schedule.next_run_at == datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    async with unit_of_work(factory) as repos:
        actions = [event.action for event in await repos.audit.list_recent()]
        assert await repos.schedules.count(poll_id=seed.poll_id) == 2
    assert actions[0] is AuditAction.SCHEDULE_CREATE


async def test_create_supports_every_kind(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)
    drafts = [
        daily_draft(kind=ScheduleKind.WEEKLY, weekdays=(2, 6)),
        daily_draft(kind=ScheduleKind.MONTHLY, day_of_month=15),
        daily_draft(
            kind=ScheduleKind.ONCE,
            time_of_day=None,
            run_at=FIXED_NOW + timedelta(days=1),
        ),
    ]

    created = [
        await service.create(poll_id=seed.poll_id, actor_user_id=ADMIN_USER_ID, draft=draft)
        for draft in drafts
    ]

    assert [schedule.kind for schedule in created] == [
        ScheduleKind.WEEKLY,
        ScheduleKind.MONTHLY,
        ScheduleKind.ONCE,
    ]
    assert all(schedule.next_run_at is not None for schedule in created)


async def test_create_reports_a_missing_poll(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _ = build(factory)

    with pytest.raises(NotFoundError, match="does not exist"):
        await service.create(poll_id=404, actor_user_id=ADMIN_USER_ID, draft=daily_draft())


async def test_create_rejects_an_archived_poll(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.polls.set_active(seed.poll_id, active=False, now=FIXED_NOW)
    service, _ = build(factory)

    with pytest.raises(ValidationError, match="archived"):
        await service.create(poll_id=seed.poll_id, actor_user_id=ADMIN_USER_ID, draft=daily_draft())


async def test_create_rejects_a_weekly_draft_without_weekdays(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    with pytest.raises(ValidationError, match="weekday"):
        await service.create(
            poll_id=seed.poll_id,
            actor_user_id=ADMIN_USER_ID,
            draft=daily_draft(kind=ScheduleKind.WEEKLY),
        )


async def test_create_rejects_day_thirty_two(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    with pytest.raises(ValidationError, match="31"):
        await service.create(
            poll_id=seed.poll_id,
            actor_user_id=ADMIN_USER_ID,
            draft=daily_draft(kind=ScheduleKind.MONTHLY, day_of_month=32),
        )


async def test_create_rejects_a_past_one_off(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    with pytest.raises(ValidationError, match="future"):
        await service.create(
            poll_id=seed.poll_id,
            actor_user_id=ADMIN_USER_ID,
            draft=daily_draft(
                kind=ScheduleKind.ONCE,
                time_of_day=None,
                run_at=FIXED_NOW - timedelta(seconds=1),
            ),
        )


async def test_pause_clears_the_next_run(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    paused = await service.pause(seed.schedule_id, actor_user_id=ADMIN_USER_ID)

    assert paused.is_paused is True
    assert paused.next_run_at is None
    async with unit_of_work(factory) as repos:
        assert [event.action for event in await repos.audit.list_recent()] == [
            AuditAction.SCHEDULE_PAUSE
        ]


async def test_pause_is_idempotent(seed: Seed, factory: async_sessionmaker[AsyncSession]) -> None:
    service, _ = build(factory)
    await service.pause(seed.schedule_id, actor_user_id=ADMIN_USER_ID)

    second = await service.pause(seed.schedule_id, actor_user_id=ADMIN_USER_ID)

    assert second.is_paused is True
    async with unit_of_work(factory) as repos:
        assert len(await repos.audit.list_recent()) == 1


async def test_pause_reports_a_missing_schedule(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _ = build(factory)

    with pytest.raises(NotFoundError, match="does not exist"):
        await service.pause(404, actor_user_id=ADMIN_USER_ID)


async def test_resume_recomputes_the_next_run(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(FIXED_NOW + timedelta(days=10))
    service, _ = build(factory, clock)
    await service.pause(seed.schedule_id, actor_user_id=ADMIN_USER_ID)

    resumed = await service.resume(seed.schedule_id, actor_user_id=ADMIN_USER_ID)

    assert resumed.is_paused is False
    assert resumed.next_run_at is not None
    assert resumed.next_run_at > clock.now()
    assert resumed.next_run_at > FIXED_NOW


async def test_resuming_an_active_schedule_recomputes_and_audits(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    resumed = await service.resume(seed.schedule_id, actor_user_id=ADMIN_USER_ID)

    assert resumed.is_paused is False
    assert resumed.next_run_at is not None
    async with unit_of_work(factory) as repos:
        actions = [event.action for event in await repos.audit.list_recent()]
    assert actions == [AuditAction.SCHEDULE_RESUME]


async def test_resume_reports_a_missing_schedule(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _ = build(factory)

    with pytest.raises(NotFoundError, match="does not exist"):
        await service.resume(404, actor_user_id=ADMIN_USER_ID)


async def test_resume_rejects_an_expired_one_off(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(FIXED_NOW)
    service, _ = build(factory, clock)
    once = await service.create(
        poll_id=seed.poll_id,
        actor_user_id=ADMIN_USER_ID,
        draft=daily_draft(
            kind=ScheduleKind.ONCE,
            time_of_day=None,
            run_at=FIXED_NOW + timedelta(hours=1),
        ),
    )
    assert once.id is not None
    await service.pause(once.id, actor_user_id=ADMIN_USER_ID)
    clock.set(FIXED_NOW + timedelta(hours=2))

    with pytest.raises(ConflictError, match="no future occurrence"):
        await service.resume(once.id, actor_user_id=ADMIN_USER_ID)


async def test_delete_removes_only_the_schedule(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    deleted = await service.delete(seed.schedule_id, actor_user_id=ADMIN_USER_ID)

    assert deleted.schedule_id == seed.schedule_id
    assert deleted.poll_id == seed.poll_id
    async with unit_of_work(factory) as repos:
        assert await repos.schedules.get(seed.schedule_id) is None
        assert await repos.polls.get(seed.poll_id) is not None
        assert [event.action for event in await repos.audit.list_recent()] == [
            AuditAction.SCHEDULE_DELETE
        ]


async def test_delete_reports_a_missing_schedule(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _ = build(factory)

    with pytest.raises(NotFoundError, match="does not exist"):
        await service.delete(404, actor_user_id=ADMIN_USER_ID)


async def test_preview_returns_five_ordered_occurrences(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    runs = await service.preview(seed.schedule_id)

    assert len(runs) == 5
    assert list(runs) == sorted(runs)


async def test_preview_reports_a_missing_schedule(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _ = build(factory)

    with pytest.raises(NotFoundError, match="does not exist"):
        await service.preview(404)


async def test_detail_includes_the_schedule_and_runs(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = build(factory)

    detail = await service.detail(seed.schedule_id)

    assert detail.schedule.id == seed.schedule_id
    assert len(detail.next_runs) == 5


async def test_page_paginates_and_filters_by_poll(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        for _ in range(8):
            await repos.schedules.add(make_schedule(id=None, poll_id=seed.poll_id), now=FIXED_NOW)
    service, _ = build(factory)

    first = await service.page(1, poll_id=seed.poll_id)
    second = await service.page(2, poll_id=seed.poll_id)

    assert first.total == 9
    assert len(first.items) == 8
    assert len(second.items) == 1
    unrelated = await service.page(1, poll_id=seed.poll_id + 100)
    assert unrelated.total == 0
