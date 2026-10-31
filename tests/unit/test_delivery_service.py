"""Delivery service behaviour (FR-125 - FR-133).

Every case uses a fixed clock, a fake sender, and a fake pacer: no sleeping, no network. Assertions
inspect the persisted row, because the row is the delivery guarantee.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import AuditAction, DeliveryStatus, SkipReason, Trigger
from telegram_auto_poll.domain.errors import (
    ConflictError,
    DeliveryError,
    NotFoundError,
    ValidationError,
)
from telegram_auto_poll.services.delivery_service import (
    MAX_RETRY_DELAY_SECONDS,
    DeliveryService,
    sanitize_error,
)
from tests.conftest import ADMIN_USER_ID, Seed
from tests.factories import FIXED_NOW, make_chat
from tests.fakes import FakeClock, FakeNotifier, FakePacer, FakeSender

OCCURRENCE = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
OTHER_CHAT_ID = -1_001_234_567_891


def build(
    factory: async_sessionmaker[AsyncSession],
    clock: FakeClock | None = None,
    **kwargs: object,
) -> tuple[DeliveryService, FakeSender, FakeNotifier, FakePacer, FakeClock]:
    resolved_clock = clock or FakeClock(OCCURRENCE)
    sender = FakeSender()
    notifier = FakeNotifier()
    pacer = FakePacer()
    service = DeliveryService(
        uow=lambda: unit_of_work(factory),
        sender=sender,
        notifier=notifier,
        pacer=pacer,
        clock=resolved_clock,
        random_source=lambda: 0.5,
        **kwargs,  # type: ignore[arg-type]
    )
    return service, sender, notifier, pacer, resolved_clock


async def add_second_chat(seed: Seed, factory: async_sessionmaker[AsyncSession]) -> int:
    async with unit_of_work(factory) as repos:
        other = await repos.chats.upsert(
            make_chat(id=None, telegram_chat_id=OTHER_CHAT_ID), now=FIXED_NOW
        )
        assert other.id is not None
        await repos.polls.set_targets(seed.poll_id, [seed.chat_id, other.id], now=FIXED_NOW)
    return other.id


async def delivery_rows(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> list[tuple[int, str, str | None, int, int | None]]:
    async with unit_of_work(factory) as repos:
        rows = await repos.deliveries.list_for_occurrence(seed.schedule_id, OCCURRENCE)
    return [
        (
            delivery.chat_id,
            delivery.status.value,
            delivery.skip_reason,
            delivery.attempt_count,
            delivery.telegram_message_id,
        )
        for delivery in rows
    ]


# --------------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------------


async def test_plan_creates_one_pending_row_and_advances_the_schedule(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, *_ = build(factory)

    outcome = await service.plan_due()

    assert outcome.schedules == 1
    assert outcome.created == 1
    assert outcome.skipped == 0
    assert await delivery_rows(seed, factory) == [
        (seed.chat_id, DeliveryStatus.PENDING.value, None, 0, None)
    ]
    async with unit_of_work(factory) as repos:
        schedule = await repos.schedules.get(seed.schedule_id)
    assert schedule is not None
    assert schedule.last_run_at == OCCURRENCE
    assert schedule.next_run_at == datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


async def test_plan_creates_one_row_per_active_target(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    other_chat_id = await add_second_chat(seed, factory)
    service, *_ = build(factory)

    outcome = await service.plan_due()

    assert outcome.created == 2
    rows = await delivery_rows(seed, factory)
    assert [row[0] for row in rows] == sorted([seed.chat_id, other_chat_id])


async def test_plan_is_idempotent_for_the_same_occurrence(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, *_ = build(factory)
    await service.plan_due()

    second = await service.plan_due()

    assert second.created == 0
    assert len(await delivery_rows(seed, factory)) == 1


async def test_plan_sends_at_the_grace_boundary(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(OCCURRENCE + timedelta(seconds=120))
    service, *_ = build(factory, clock)

    outcome = await service.plan_due()

    assert outcome.created == 1
    assert (await delivery_rows(seed, factory))[0][1:] == (
        DeliveryStatus.PENDING.value,
        None,
        0,
        None,
    )


async def test_plan_skips_an_occurrence_beyond_the_grace_window(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    clock = FakeClock(OCCURRENCE + timedelta(seconds=121))
    service, *_ = build(factory, clock)

    outcome = await service.plan_due()

    assert outcome.created == 0
    assert outcome.skipped == 1
    rows = await delivery_rows(seed, factory)
    assert rows == [
        (seed.chat_id, DeliveryStatus.SKIPPED.value, SkipReason.MISSED_GRACE_WINDOW.value, 0, None)
    ]
    async with unit_of_work(factory) as repos:
        schedule = await repos.schedules.get(seed.schedule_id)
    assert schedule is not None
    assert schedule.next_run_at == datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


async def test_plan_skips_an_archived_poll(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.polls.set_active(seed.poll_id, active=False, now=FIXED_NOW)
    service, *_ = build(factory)

    await service.plan_due()

    rows = await delivery_rows(seed, factory)
    assert rows[0][1:] == (DeliveryStatus.SKIPPED.value, SkipReason.POLL_ARCHIVED.value, 0, None)


async def test_plan_skips_an_inactive_chat(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    other_chat_id = await add_second_chat(seed, factory)
    async with unit_of_work(factory) as repos:
        await repos.chats.set_active(seed.chat_id, active=False, now=FIXED_NOW)
    service, *_ = build(factory)

    outcome = await service.plan_due()

    assert outcome.created == 1
    assert outcome.skipped == 1
    rows = {row[0]: row for row in await delivery_rows(seed, factory)}
    assert rows[other_chat_id][1] == DeliveryStatus.PENDING.value
    assert rows[seed.chat_id][1] == DeliveryStatus.SKIPPED.value
    assert rows[seed.chat_id][2] == SkipReason.CHAT_INACTIVE.value


async def test_plan_ignores_a_schedule_that_disappeared(
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram_auto_poll.db.repositories.schedules import ScheduleRepository

    async def missing(self: ScheduleRepository, moment: datetime, *, limit: int = 50) -> list[int]:
        return [seed.schedule_id + 10_000]

    monkeypatch.setattr(ScheduleRepository, "due_ids_before", missing)
    service, *_ = build(factory)

    outcome = await service.plan_due()

    assert outcome.schedules == 0


async def test_plan_ignores_a_schedule_whose_poll_disappeared(
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram_auto_poll.db.repositories.polls import PollRepository

    async def missing(self: PollRepository, poll_id: int) -> None:
        return None

    monkeypatch.setattr(PollRepository, "get", missing)
    service, *_ = build(factory)

    outcome = await service.plan_due()

    assert outcome.schedules == 0


async def test_plan_ignores_a_paused_schedule(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.schedules.set_paused(
            seed.schedule_id, paused=True, next_run_at=None, now=FIXED_NOW
        )
    service, *_ = build(factory)

    outcome = await service.plan_due()

    assert outcome.schedules == 0
    assert outcome.created == 0


# --------------------------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------------------------


async def test_dispatch_sends_the_exact_poll_payload(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, _, pacer, _ = build(factory)
    await service.plan_due()

    outcomes = await service.dispatch()

    assert [outcome.status for outcome in outcomes] == [DeliveryStatus.SENT]
    request = sender.requests[0]
    assert request.chat_id == seed.chat.telegram_chat_id
    assert request.question == seed.poll.question
    assert request.options == seed.poll.options
    assert request.description == seed.poll.description
    assert request.is_anonymous is True
    assert request.allows_multiple_answers is False
    assert pacer.chats == [seed.chat.telegram_chat_id]
    rows = await delivery_rows(seed, factory)
    assert rows[0][1] == DeliveryStatus.SENT.value
    assert rows[0][4] is not None


async def test_dispatch_writes_an_audit_row(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, *_ = build(factory)
    await service.plan_due()
    await service.dispatch()

    async with unit_of_work(factory) as repos:
        events = await repos.audit.list_recent()
    assert [event.action for event in events] == [AuditAction.DELIVERY_SEND]
    assert events[0].details is not None
    assert events[0].details["status"] == "sent"


async def test_dispatch_retries_a_retryable_error_with_backoff(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, notifier, _, clock = build(factory)
    await service.plan_due()
    sender.fail_next(DeliveryError("read timeout", retryable=True))

    first = await service.dispatch()

    assert first[0].status is DeliveryStatus.PENDING
    assert first[0].attempts == 1
    assert notifier.messages == []
    rows = await delivery_rows(seed, factory)
    assert rows[0][1] == DeliveryStatus.PENDING.value
    assert rows[0][3] == 1

    clock.advance(2)
    second = await service.dispatch()

    assert second[0].status is DeliveryStatus.SENT
    assert second[0].attempts == 2
    assert len(sender.requests) == 2


async def test_dispatch_honors_retry_after(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, _, _, clock = build(factory, retry_base_seconds=1.0)
    await service.plan_due()
    sender.fail_next(DeliveryError("rate limited", retryable=True, retry_after=7.0))

    await service.dispatch()

    async with unit_of_work(factory) as repos:
        claimed = await repos.deliveries.claim_due(clock.now(), limit=10)
    assert claimed == []
    clock.advance(7)
    async with unit_of_work(factory) as repos:
        assert len(await repos.deliveries.claim_due(clock.now(), limit=10)) == 1


async def test_dispatch_caps_retry_after_at_five_minutes(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, _, _, clock = build(factory)
    await service.plan_due()
    sender.fail_next(DeliveryError("slow down", retryable=True, retry_after=1_000.0))

    await service.dispatch()

    async with unit_of_work(factory) as repos:
        row = (await repos.deliveries.list_for_occurrence(seed.schedule_id, OCCURRENCE))[0]
    assert row.next_attempt_at == clock.now() + timedelta(seconds=MAX_RETRY_DELAY_SECONDS)
    assert isinstance(row.next_attempt_at, datetime)
    assert row.next_attempt_at.tzinfo is UTC


async def test_dispatch_fails_a_non_retryable_error_immediately(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, notifier, _, _ = build(factory)
    await service.plan_due()
    sender.fail_next(DeliveryError("chat not found", retryable=False))

    outcomes = await service.dispatch()

    assert [outcome.status for outcome in outcomes] == [DeliveryStatus.FAILED]
    assert len(notifier.messages) == 1
    assert "failed permanently" in notifier.messages[0]
    rows = await delivery_rows(seed, factory)
    assert rows[0][1] == DeliveryStatus.FAILED.value


async def test_dispatch_fails_after_the_last_allowed_attempt(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, notifier, _, _ = build(factory, max_attempts=1)
    await service.plan_due()
    sender.fail_next(DeliveryError("timeout", retryable=True))

    outcomes = await service.dispatch()

    assert [outcome.status for outcome in outcomes] == [DeliveryStatus.FAILED]
    assert len(notifier.messages) == 1


async def test_dispatch_skips_an_inactive_chat_without_calling_telegram(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, _, pacer, _ = build(factory)
    await service.plan_due()
    async with unit_of_work(factory) as repos:
        await repos.chats.set_active(seed.chat_id, active=False, now=FIXED_NOW)

    outcomes = await service.dispatch()

    assert [outcome.status for outcome in outcomes] == [DeliveryStatus.SKIPPED]
    assert sender.requests == []
    assert pacer.chats == []
    rows = await delivery_rows(seed, factory)
    assert rows[0][2] == SkipReason.CHAT_INACTIVE.value


async def test_dispatch_skips_an_archived_poll(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, _, _, _ = build(factory)
    await service.plan_due()
    async with unit_of_work(factory) as repos:
        await repos.polls.set_active(seed.poll_id, active=False, now=FIXED_NOW)

    outcomes = await service.dispatch()

    assert [outcome.status for outcome in outcomes] == [DeliveryStatus.SKIPPED]
    assert sender.requests == []
    rows = await delivery_rows(seed, factory)
    assert rows[0][2] == SkipReason.POLL_ARCHIVED.value


async def test_dispatch_skips_a_poll_that_vanished(
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram_auto_poll.db.repositories.polls import PollRepository

    service, sender, _, _, _ = build(factory)
    await service.plan_due()

    async def missing(self: PollRepository, poll_id: int) -> None:
        return None

    monkeypatch.setattr(PollRepository, "get", missing)
    outcomes = await service.dispatch()

    assert [outcome.status for outcome in outcomes] == [DeliveryStatus.SKIPPED]
    assert sender.requests == []
    rows = await delivery_rows(seed, factory)
    assert rows[0][2] == SkipReason.POLL_MISSING.value


async def test_dispatch_respects_the_batch_limit(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    other_chat_id = await add_second_chat(seed, factory)
    third = -1_001_234_567_892
    async with unit_of_work(factory) as repos:
        created = await repos.chats.upsert(
            make_chat(id=None, telegram_chat_id=third), now=FIXED_NOW
        )
        assert created.id is not None
        await repos.polls.set_targets(
            seed.poll_id, [seed.chat_id, other_chat_id, created.id], now=FIXED_NOW
        )
    service, *_ = build(factory)
    await service.plan_due()

    first = await service.dispatch(limit=2)
    second = await service.dispatch(limit=2)

    assert len(first) == 2
    assert len(second) == 1


# --------------------------------------------------------------------------------------------
# Manual sends
# --------------------------------------------------------------------------------------------


async def test_send_now_adds_manual_rows_alongside_scheduled_ones(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, _, _, _ = build(factory)
    await service.plan_due()

    created = await service.send_now(seed.poll_id, actor_user_id=ADMIN_USER_ID)
    outcomes = await service.dispatch()

    assert created == 1
    assert len(outcomes) == 2
    assert len(sender.requests) == 2
    async with unit_of_work(factory) as repos:
        counts = await repos.deliveries.counts()
    assert counts[DeliveryStatus.SENT] == 2

    async with unit_of_work(factory) as repos:
        recent = await repos.deliveries.list_recent(limit=10)
    assert {delivery.trigger for delivery in recent} == {Trigger.SCHEDULED, Trigger.MANUAL}


async def test_send_now_rejects_a_missing_poll(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, *_ = build(factory)

    with pytest.raises(NotFoundError, match="does not exist"):
        await service.send_now(404, actor_user_id=ADMIN_USER_ID)


async def test_send_now_rejects_an_archived_poll(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.polls.set_active(seed.poll_id, active=False, now=FIXED_NOW)
    service, *_ = build(factory)

    with pytest.raises(ValidationError, match="archived"):
        await service.send_now(seed.poll_id, actor_user_id=ADMIN_USER_ID)


async def test_send_now_rejects_a_poll_without_active_targets(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.chats.set_active(seed.chat_id, active=False, now=FIXED_NOW)
    service, *_ = build(factory)

    with pytest.raises(ConflictError, match="no active target"):
        await service.send_now(seed.poll_id, actor_user_id=ADMIN_USER_ID)


# --------------------------------------------------------------------------------------------
# Recovery and error text
# --------------------------------------------------------------------------------------------


async def test_recover_fails_rows_left_sending(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, *_ = build(factory)
    await service.plan_due()
    async with unit_of_work(factory) as repos:
        claimed = await repos.deliveries.claim_due(FIXED_NOW + timedelta(minutes=1), limit=10)
    assert len(claimed) == 1

    assert await service.recover() == 1
    assert await service.recover() == 0
    async with unit_of_work(factory) as repos:
        failures = await repos.deliveries.list_recent_failures()
    assert failures[0].status is DeliveryStatus.FAILED
    assert failures[0].last_error == "interrupted before the result was recorded"


def test_sanitize_error_removes_token_shaped_secrets() -> None:
    token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"

    cleaned = sanitize_error(f"Unauthorized for bot {token}")

    assert token not in cleaned
    assert "[redacted]" in cleaned


def test_sanitize_error_bounds_the_length() -> None:
    assert len(sanitize_error("x" * 2_000)) == 500


def test_sanitize_error_never_returns_an_empty_string() -> None:
    assert sanitize_error("   ") == "unknown error"


async def test_dispatch_sanitizes_a_leaked_token(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, sender, _, _, _ = build(factory)
    await service.plan_due()
    sender.fail_next(
        DeliveryError(
            "Unauthorized 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            retryable=False,
        )
    )

    await service.dispatch()

    async with unit_of_work(factory) as repos:
        failure = (await repos.deliveries.list_recent_failures())[0]
    assert failure.last_error is not None
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in failure.last_error
