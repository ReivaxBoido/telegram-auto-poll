"""Startup recovery (T-036, FR-104, FR-128): no resend of an ambiguous row, no lost occurrence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import DeliveryStatus, SkipReason
from telegram_auto_poll.services.delivery_service import DeliveryService
from telegram_auto_poll.services.recovery_service import (
    AMBIGUOUS_OUTCOME_REASON,
    RecoveryResult,
    RecoveryService,
)
from tests.conftest import Seed
from tests.factories import make_delivery
from tests.fakes import FakeClock, FakeNotifier, FakePacer, FakeSender

OCCURRENCE = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def build(
    factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> tuple[RecoveryService, DeliveryService, FakeNotifier, FakeSender]:
    notifier = FakeNotifier()
    sender = FakeSender()
    delivery = DeliveryService(
        uow=lambda: unit_of_work(factory),
        sender=sender,
        notifier=notifier,
        pacer=FakePacer(),
        clock=clock,
        random_source=lambda: 0.5,
    )
    recovery = RecoveryService(
        uow=lambda: unit_of_work(factory),
        clock=clock,
        notifier=notifier,
        plan=delivery.plan_due,
    )
    return recovery, delivery, notifier, sender


async def test_a_row_left_sending_fails_without_being_resent(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(OCCURRENCE + timedelta(minutes=10))
    recovery, _, notifier, sender = build(factory, clock)
    async with unit_of_work(factory) as repos:
        await repos.deliveries.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=seed.schedule_id,
                    scheduled_for=OCCURRENCE,
                )
            ],
            now=OCCURRENCE,
        )
        claimed = await repos.deliveries.claim_due(OCCURRENCE, limit=1)
    assert len(claimed) == 1

    result = await recovery.run()

    assert result.failed_deliveries == 1
    assert sender.requests == []
    async with unit_of_work(factory) as repos:
        failures = await repos.deliveries.list_recent_failures()
    assert failures[0].status is DeliveryStatus.FAILED
    assert failures[0].last_error == AMBIGUOUS_OUTCOME_REASON
    assert len(notifier.messages) == 1
    assert "will not be retried" in notifier.messages[0]


async def test_recovery_does_not_alert_twice(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(OCCURRENCE + timedelta(minutes=10))
    recovery, _, notifier, _ = build(factory, clock)
    async with unit_of_work(factory) as repos:
        await repos.deliveries.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=seed.schedule_id,
                    scheduled_for=OCCURRENCE,
                )
            ],
            now=OCCURRENCE,
        )
        await repos.deliveries.claim_due(OCCURRENCE, limit=1)

    await recovery.run()
    second = await recovery.run()

    assert second.failed_deliveries == 0
    assert len(notifier.messages) == 1


async def test_a_missed_occurrence_is_skipped_after_the_grace_window(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(OCCURRENCE + timedelta(seconds=121))
    recovery, _, notifier, _ = build(factory, clock)

    result = await recovery.run()

    assert result.skipped_deliveries == 1
    assert result.created_deliveries == 0
    async with unit_of_work(factory) as repos:
        rows = await repos.deliveries.list_for_occurrence(seed.schedule_id, OCCURRENCE)
        schedule = await repos.schedules.get(seed.schedule_id)
    assert rows[0].status is DeliveryStatus.SKIPPED
    assert rows[0].skip_reason == SkipReason.MISSED_GRACE_WINDOW.value
    assert schedule is not None
    assert schedule.next_run_at == datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    assert notifier.messages == []


async def test_a_within_grace_occurrence_is_planned_and_sent(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(OCCURRENCE + timedelta(seconds=30))
    recovery, delivery, _, sender = build(factory, clock)

    result = await recovery.run()
    outcomes = await delivery.dispatch()

    assert result.created_deliveries == 1
    assert [outcome.status for outcome in outcomes] == [DeliveryStatus.SENT]
    assert len(sender.requests) == 1


async def test_clean_startup_changes_nothing(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(OCCURRENCE + timedelta(hours=1))
    recovery, _, notifier, _ = build(factory, clock)

    result = await recovery.run()

    assert result == RecoveryResult(
        failed_deliveries=0,
        planned_occurrences=1,
        created_deliveries=0,
        skipped_deliveries=1,
    )
    assert notifier.messages == []
