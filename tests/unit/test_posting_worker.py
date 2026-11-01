"""Posting worker (SPEC-003 FR-331 - FR-335)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.chats import ChatRepository
from telegram_auto_poll.db.repositories.poll_instances import PollInstanceRepository
from telegram_auto_poll.db.repositories.poll_messages import PollMessageRepository
from telegram_auto_poll.db.repositories.polls import PollRepository
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import InstanceStatus, MessageStatus, PollStatus
from telegram_auto_poll.domain.errors import DeliveryError
from telegram_auto_poll.services.posting_worker import (
    AMBIGUOUS_OUTCOME_REASON,
    PostingWorker,
    fail_stale_sending,
)
from tests.conftest import Seed
from tests.factories import FIXED_NOW
from tests.fakes import (
    FakeClock,
    FakeMessageGateway,
    FakeNotifier,
    FakeObserver,
    FakePacer,
    FakeSleeper,
)


@pytest.fixture
def gateway() -> FakeMessageGateway:
    return FakeMessageGateway()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def observer() -> FakeObserver:
    return FakeObserver()


@pytest.fixture
def worker(
    factory: async_sessionmaker[AsyncSession],
    gateway: FakeMessageGateway,
    notifier: FakeNotifier,
    observer: FakeObserver,
) -> PostingWorker:
    return PostingWorker(
        uow=lambda: unit_of_work(factory),
        gateway=gateway,
        pacer=FakePacer(),
        notifier=notifier,
        clock=FakeClock(FIXED_NOW),
        sleeper=FakeSleeper().sleep,
        names_per_option=10,
        max_attempts=2,
        retry_base_seconds=0,
        concurrency=2,
        tick_seconds=1,
        observer=observer,
    )


async def _add_pending(seed: Seed, factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_scope(factory) as session:
        await PollMessageRepository(session).reset_for_retry(
            seed.message_id, next_attempt_at=FIXED_NOW, now=FIXED_NOW
        )
    return seed.message_id


async def test_tick_sends_a_pending_message(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    observer: FakeObserver,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    message_id = await _add_pending(seed, factory)

    sent = await worker.tick()

    assert sent == 1
    assert len(gateway.sent) == 1
    assert gateway.sent[0]["chat_id"] == seed.chat.telegram_chat_id
    assert observer.sent_count == 1
    async with session_scope(factory) as session:
        message = await PollMessageRepository(session).get(message_id)
    assert message is not None
    assert message.status is MessageStatus.SENT


async def test_retryable_failure_is_rescheduled_then_succeeds(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    message_id = await _add_pending(seed, factory)
    gateway.send_failures.append(DeliveryError("timeout", retryable=True, classification="timeout"))

    await worker.tick()
    async with session_scope(factory) as session:
        first = await PollMessageRepository(session).get(message_id)
    assert first is not None
    assert first.status is MessageStatus.PENDING
    assert first.attempt_count == 1

    await worker.tick()
    async with session_scope(factory) as session:
        second = await PollMessageRepository(session).get(message_id)
    assert second is not None
    assert second.status is MessageStatus.SENT
    assert second.attempt_count == 2


async def test_terminal_failure_alerts_once(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    notifier: FakeNotifier,
    observer: FakeObserver,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    message_id = await _add_pending(seed, factory)
    gateway.send_failures.append(
        DeliveryError("forbidden", retryable=False, classification="forbidden")
    )

    await worker.tick()

    assert len(notifier.messages) == 1
    assert observer.failed_reasons == ["forbidden"]
    async with session_scope(factory) as session:
        message = await PollMessageRepository(session).get(message_id)
    assert message is not None
    assert message.status is MessageStatus.FAILED


async def test_inactive_chat_is_skipped(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    observer: FakeObserver,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _add_pending(seed, factory)
    async with session_scope(factory) as session:
        await ChatRepository(session).set_active(seed.chat_id, active=False, now=FIXED_NOW)

    await worker.tick()

    assert gateway.sent == []
    assert observer.skipped_count == 1


async def test_closed_instance_is_skipped(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _add_pending(seed, factory)
    async with session_scope(factory) as session:
        await PollInstanceRepository(session).set_status(
            seed.instance_id, InstanceStatus.CLOSED, now=FIXED_NOW, closed_at=FIXED_NOW
        )

    await worker.tick()

    assert gateway.sent == []


async def test_deleted_poll_is_skipped(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _add_pending(seed, factory)
    async with session_scope(factory) as session:
        await PollRepository(session).set_status(seed.poll_id, PollStatus.DELETED, now=FIXED_NOW)

    await worker.tick()

    assert gateway.sent == []


async def test_no_pending_returns_zero(worker: PostingWorker) -> None:
    assert await worker.tick() == 0


async def test_wake_sets_the_event(worker: PostingWorker) -> None:
    worker.wake()
    assert worker._wake.is_set()


async def test_run_stops_cleanly(worker: PostingWorker, gateway: FakeMessageGateway) -> None:
    stop = asyncio.Event()
    stop.set()
    await worker.run(stop)
    assert gateway.sent == []


async def test_fail_stale_sending_marks_rows_and_alerts(
    factory: async_sessionmaker[AsyncSession], seed: Seed, notifier: FakeNotifier
) -> None:
    async with session_scope(factory) as session:
        await session.execute(
            text("UPDATE poll_messages SET status = 'sending' WHERE id = :id"),
            {"id": seed.message_id},
        )

    failed = await fail_stale_sending(lambda: unit_of_work(factory), FakeClock(FIXED_NOW), notifier)

    assert failed == 1
    assert len(notifier.messages) == 1
    assert AMBIGUOUS_OUTCOME_REASON in notifier.messages[0]


async def test_null_observer_paths(
    factory: async_sessionmaker[AsyncSession],
    gateway: FakeMessageGateway,
    notifier: FakeNotifier,
    seed: Seed,
) -> None:
    worker = PostingWorker(
        uow=lambda: unit_of_work(factory),
        gateway=gateway,
        pacer=FakePacer(),
        notifier=notifier,
        clock=FakeClock(FIXED_NOW),
        sleeper=FakeSleeper().sleep,
        names_per_option=10,
        max_attempts=1,
        retry_base_seconds=0,
        concurrency=1,
    )
    await _add_pending(seed, factory)
    await worker.tick()

    await _add_pending(seed, factory)
    gateway.send_failures.append(
        DeliveryError("bad", retryable=False, classification="bad_request")
    )
    await worker.tick()

    await _add_pending(seed, factory)
    async with session_scope(factory) as session:
        await ChatRepository(session).set_active(seed.chat_id, active=False, now=FIXED_NOW)
    await worker.tick()


async def test_run_processes_a_tick_then_stops(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _add_pending(seed, factory)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.sleep(0)
    stop.set()
    await task
    assert len(gateway.sent) == 1


async def test_render_failure_skips(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import telegram_auto_poll.services.posting_worker as module

    def boom(*args: object, **kwargs: object) -> object:
        raise ValueError("too long")

    monkeypatch.setattr(module, "render_poll_message", boom)
    await _add_pending(seed, factory)
    await worker.tick()
    assert gateway.sent == []


async def test_missing_instance_or_poll_is_skipped(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram_auto_poll.db.repositories.poll_instances import PollInstanceRepository
    from telegram_auto_poll.db.repositories.polls import PollRepository

    async def no_instance(self: object, instance_id: int) -> None:
        return None

    monkeypatch.setattr(PollInstanceRepository, "get", no_instance)
    await _add_pending(seed, factory)
    await worker.tick()
    assert gateway.sent == []

    monkeypatch.undo()

    async def no_poll(self: object, poll_id: int) -> None:
        return None

    monkeypatch.setattr(PollRepository, "get", no_poll)
    await _add_pending(seed, factory)
    await worker.tick()
    assert gateway.sent == []


async def test_inline_message_without_chat_is_skipped(
    worker: PostingWorker,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await session.execute(
            text(
                "UPDATE poll_messages SET chat_id = NULL, inline_message_id = 'inline' "
                "WHERE id = :id"
            ),
            {"id": seed.message_id},
        )
        await PollMessageRepository(session).reset_for_retry(
            seed.message_id, next_attempt_at=FIXED_NOW, now=FIXED_NOW
        )
    await worker.tick()
    assert gateway.sent == []


def test_backoff_clamps_retry_after(
    factory: async_sessionmaker[AsyncSession],
    gateway: FakeMessageGateway,
    notifier: FakeNotifier,
) -> None:
    worker = PostingWorker(
        uow=lambda: unit_of_work(factory),
        gateway=gateway,
        pacer=FakePacer(),
        notifier=notifier,
        clock=FakeClock(FIXED_NOW),
        sleeper=FakeSleeper().sleep,
        names_per_option=10,
        max_attempts=2,
        retry_base_seconds=1,
        concurrency=1,
    )
    assert worker._backoff(1, 999) == 300
    assert worker._backoff(1, 0.5) == 1


async def test_fail_stale_sending_with_no_rows(
    factory: async_sessionmaker[AsyncSession], notifier: FakeNotifier
) -> None:
    failed = await fail_stale_sending(lambda: unit_of_work(factory), FakeClock(FIXED_NOW), notifier)
    assert failed == 0
    assert notifier.messages == []
