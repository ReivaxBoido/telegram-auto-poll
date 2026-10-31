"""Audit and retention services (FR-134, FR-135)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.models import AuditEventRow, DeliveryRow
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import AuditAction, DeliveryStatus
from telegram_auto_poll.services.audit_service import AuditService
from telegram_auto_poll.services.retention_service import RetentionService
from tests.conftest import ADMIN_USER_ID, Seed
from tests.factories import FIXED_NOW, make_delivery
from tests.fakes import FakeClock


class FakeCheckpoint:
    """Records WAL checkpoint requests instead of touching a database."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1


# --------------------------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------------------------


async def test_record_writes_inside_the_callers_transaction(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = AuditService(uow=lambda: unit_of_work(factory))

    async with unit_of_work(factory) as repos:
        event = await service.record(
            repos,
            actor_user_id=ADMIN_USER_ID,
            action=AuditAction.POLL_CREATE,
            entity_type="poll",
            entity_id=42,
            details={"question": "Ship it?"},
            now=FIXED_NOW,
        )

    assert event.id is not None
    assert event.entity_id == 42

    recent = await service.recent()
    assert [record.action for record in recent] == [AuditAction.POLL_CREATE]


async def test_a_rolled_back_mutation_leaves_no_audit_row(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = AuditService(uow=lambda: unit_of_work(factory))

    try:
        async with unit_of_work(factory) as repos:
            await service.record(
                repos,
                actor_user_id=ADMIN_USER_ID,
                action=AuditAction.CHAT_REGISTER,
                entity_type="chat",
                entity_id=1,
                now=FIXED_NOW,
            )
            raise RuntimeError("abort")
    except RuntimeError:
        pass

    assert await service.recent() == []


async def test_history_is_scoped_to_one_entity(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = AuditService(uow=lambda: unit_of_work(factory))
    async with unit_of_work(factory) as repos:
        await service.record(
            repos,
            actor_user_id=ADMIN_USER_ID,
            action=AuditAction.POLL_CREATE,
            entity_type="poll",
            entity_id=7,
            now=FIXED_NOW,
        )
        await service.record(
            repos,
            actor_user_id=ADMIN_USER_ID,
            action=AuditAction.SCHEDULE_CREATE,
            entity_type="schedule",
            entity_id=7,
            now=FIXED_NOW,
        )

    history = await service.history("poll", 7)

    assert [event.action for event in history] == [AuditAction.POLL_CREATE]


# --------------------------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------------------------


def build_retention(
    factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = 1_000,
    retention_days: int = 90,
) -> tuple[RetentionService, FakeCheckpoint]:
    checkpoint = FakeCheckpoint()
    service = RetentionService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        checkpoint=checkpoint,
        retention_days=retention_days,
        batch_size=batch_size,
    )
    return service, checkpoint


def _delivery_values(scheduled_for: int) -> dict[str, object]:
    return {
        "poll_id": 1,
        "chat_id": 1,
        "schedule_id": None,
        "trigger": "manual",
        "scheduled_for_utc": scheduled_for,
        "status": "sent",
        "attempt_count": 1,
        "created_at": scheduled_for,
        "completed_at": scheduled_for,
    }


async def test_retention_deletes_old_rows_in_batches_and_is_idempotent(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.deliveries.insert_many_ignore(
            [
                # The seed poll and chat exist, so the foreign keys hold.
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=None,
                    scheduled_for=FIXED_NOW - timedelta(days=200 - index),
                )
                for index in range(5)
            ],
            now=FIXED_NOW,
        )
        await repos.deliveries.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=None,
                    scheduled_for=FIXED_NOW - timedelta(days=1),
                )
            ],
            now=FIXED_NOW,
        )

    service, checkpoint = build_retention(factory, batch_size=2)
    first = await service.run()

    assert first.deliveries == 5
    assert first.batches == 4  # 2 + 2 + 1 deliveries, then one empty audit batch
    assert checkpoint.calls == 1

    second = await service.run()

    assert second.deliveries == 0
    assert second.audit_events == 0
    assert checkpoint.calls == 2

    async with unit_of_work(factory) as repos:
        remaining = await repos.deliveries.counts()
    assert remaining[DeliveryStatus.PENDING] == 1


async def test_retention_uses_the_configured_day_window(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.deliveries.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=None,
                    scheduled_for=FIXED_NOW - timedelta(days=10),
                ),
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=None,
                    scheduled_for=FIXED_NOW - timedelta(days=1),
                ),
            ],
            now=FIXED_NOW,
        )

    service, _ = build_retention(factory, retention_days=5)
    result = await service.run()

    assert result.deliveries == 1
    assert result.cutoff == (FIXED_NOW - timedelta(days=5)).isoformat()


async def test_retention_deletes_old_audit_events(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    old = int((FIXED_NOW - timedelta(days=200)).timestamp())
    fresh = int((FIXED_NOW - timedelta(days=1)).timestamp())
    async with factory() as session:
        await session.execute(
            insert(AuditEventRow),
            [
                {
                    "actor_user_id": 1,
                    "action": "poll.create",
                    "entity_type": "poll",
                    "created_at": old,
                },
                {
                    "actor_user_id": 1,
                    "action": "poll.create",
                    "entity_type": "poll",
                    "created_at": fresh,
                },
            ],
        )
        await session.commit()

    service, _ = build_retention(factory)
    result = await service.run()

    assert result.audit_events == 1
    async with unit_of_work(factory) as repos:
        assert len(await repos.audit.list_recent()) == 1


async def test_retention_handles_an_empty_database(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, checkpoint = build_retention(factory)

    result = await service.run()

    assert result.deliveries == 0
    assert result.audit_events == 0
    assert result.batches == 2
    assert checkpoint.calls == 1


async def test_retention_with_the_production_batch_size(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as session:
        await session.execute(
            insert(DeliveryRow),
            [
                _delivery_values(int((FIXED_NOW - timedelta(days=200)).timestamp()) + index)
                for index in range(2_500)
            ],
        )
        await session.commit()

    service, _ = build_retention(factory)
    result = await service.run()

    assert result.deliveries == 2_500
    assert result.batches == 4  # 1_000 + 1_000 + 500 deliveries, then one empty audit batch


async def test_retention_keeps_batching_while_a_batch_is_full(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    old = int((FIXED_NOW - timedelta(days=200)).timestamp())
    async with factory() as session:
        await session.execute(
            insert(AuditEventRow),
            [
                {
                    "actor_user_id": 1,
                    "action": "poll.create",
                    "entity_type": "poll",
                    "created_at": old + offset,
                }
                for offset in range(2)
            ],
        )
        await session.commit()

    service, checkpoint = build_retention(factory, batch_size=1)
    result = await service.run()

    assert result.audit_events == 2
    assert result.batches == 4  # one empty delivery batch, then 1 + 1 + 0 audit batches
    assert checkpoint.calls == 1
