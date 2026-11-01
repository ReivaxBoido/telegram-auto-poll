"""Response broadcaster (SPEC-003 FR-322, FR-329, FR-330)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.poll_messages import PollMessageRepository
from telegram_auto_poll.db.repositories.poll_responses import PollResponseRepository
from telegram_auto_poll.db.repositories.polls import PollRepository
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import MessageStatus, PollStatus
from telegram_auto_poll.domain.errors import DeliveryError
from telegram_auto_poll.services.broadcaster import ResponseBroadcaster
from tests.conftest import Seed
from tests.factories import FIXED_NOW
from tests.fakes import FakeClock, FakeMessageGateway, FakeSleeper


@pytest.fixture
def gateway() -> FakeMessageGateway:
    return FakeMessageGateway()


@pytest.fixture
def broadcaster(
    factory: async_sessionmaker[AsyncSession], gateway: FakeMessageGateway
) -> ResponseBroadcaster:
    return ResponseBroadcaster(
        uow=lambda: unit_of_work(factory),
        gateway=gateway,
        clock=FakeClock(FIXED_NOW),
        sleeper=FakeSleeper(),
        names_per_option=10,
        debounce_seconds=0,
        concurrency=2,
        max_attempts=2,
        retry_base_seconds=1,
    )


async def test_refresh_edits_every_sent_message(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
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

    result = await broadcaster.refresh(seed.instance_id)

    assert result.edited == 1
    assert "Alice" in str(gateway.edits[0]["text"])
    assert gateway.edits[0]["message_id"] == seed.message.telegram_message_id


async def test_redact_removes_names_and_keyboard(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
    seed: Seed,
) -> None:
    result = await broadcaster.redact([seed.instance_id])

    assert result.edited == 1
    assert gateway.edits[0]["text"] == "This poll was deleted."
    assert gateway.edits[0]["keyboard"] is None


async def test_mark_dirty_process_pending_coalesces(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
    seed: Seed,
) -> None:
    broadcaster.mark_dirty(seed.instance_id)
    broadcaster.mark_dirty(seed.instance_id)

    result = await broadcaster.process_pending()

    assert result.edited == 1
    assert len(gateway.edits) == 1


async def test_run_processes_dirty_then_flushes(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
    seed: Seed,
) -> None:
    stop = asyncio.Event()
    broadcaster.mark_dirty(seed.instance_id)
    task = asyncio.create_task(broadcaster.run(stop))
    await asyncio.sleep(0)
    stop.set()
    await task

    assert len(gateway.edits) >= 1


async def test_missing_message_is_recorded(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway.edit_failures.append(
        DeliveryError("gone", retryable=False, classification="message_not_found")
    )

    result = await broadcaster.refresh(seed.instance_id)

    assert result.missing == 1
    async with session_scope(factory) as session:
        message = await PollMessageRepository(session).get(seed.message_id)
    assert message is not None
    assert message.status is MessageStatus.MISSING


async def test_retryable_edit_retries_then_fails(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
    seed: Seed,
) -> None:
    gateway.edit_failures.extend(
        [
            DeliveryError("timeout", retryable=True, classification="timeout"),
            DeliveryError("timeout", retryable=True, classification="timeout"),
        ]
    )

    result = await broadcaster.refresh(seed.instance_id)

    assert result.failed == 1
    assert len(gateway.edits) == 2


async def test_retryable_edit_succeeds_on_a_later_attempt(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
    seed: Seed,
) -> None:
    gateway.edit_failures.append(DeliveryError("timeout", retryable=True, classification="timeout"))

    result = await broadcaster.refresh(seed.instance_id)

    assert result.edited == 1
    assert len(gateway.edits) == 2


async def test_retry_after_is_clamped_in_backoff(
    broadcaster: ResponseBroadcaster, gateway: FakeMessageGateway, seed: Seed
) -> None:
    gateway.edit_failures.append(
        DeliveryError("rate", retryable=True, classification="retry_after", retry_after=999)
    )

    result = await broadcaster.refresh(seed.instance_id)

    assert result.edited == 1


async def test_refresh_unknown_instance_is_a_noop(
    broadcaster: ResponseBroadcaster, gateway: FakeMessageGateway
) -> None:
    result = await broadcaster.refresh(999)

    assert result.edited == 0
    assert gateway.edits == []


async def test_deleted_poll_renders_redacted(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await PollRepository(session).set_status(seed.poll_id, PollStatus.DELETED, now=FIXED_NOW)

    await broadcaster.refresh(seed.instance_id)

    assert gateway.edits[0]["text"] == "This poll was deleted."


async def test_run_with_debounce_sleeps_and_processes(
    factory: async_sessionmaker[AsyncSession], gateway: FakeMessageGateway, seed: Seed
) -> None:
    class SignalSleeper:
        def __init__(self) -> None:
            self.waits: list[float] = []
            self.called = asyncio.Event()

        async def sleep(self, seconds: float) -> None:
            self.waits.append(seconds)
            self.called.set()

    sleeper = SignalSleeper()
    broadcaster = ResponseBroadcaster(
        uow=lambda: unit_of_work(factory),
        gateway=gateway,
        clock=FakeClock(FIXED_NOW),
        sleeper=sleeper,  # type: ignore[arg-type]
        names_per_option=10,
        debounce_seconds=1,
    )
    broadcaster.mark_dirty(seed.instance_id)
    stop = asyncio.Event()
    task = asyncio.create_task(broadcaster.run(stop))
    await asyncio.wait_for(sleeper.called.wait(), timeout=1)
    stop.set()
    await task
    assert sleeper.waits == [1]
    assert len(gateway.edits) == 1


async def test_missing_poll_is_a_noop(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
    seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram_auto_poll.db.repositories.polls import PollRepository

    async def no_poll(self: object, poll_id: int) -> None:
        return None

    monkeypatch.setattr(PollRepository, "get", no_poll)
    result = await broadcaster.refresh(seed.instance_id)
    assert result.edited == 0
    assert gateway.edits == []


async def test_mark_missing_without_id_is_a_noop(broadcaster: ResponseBroadcaster) -> None:
    from tests.factories import make_message

    await broadcaster._mark_missing(make_message(id=None))


async def test_zero_attempts_fails_without_calling_the_gateway(
    factory: async_sessionmaker[AsyncSession], gateway: FakeMessageGateway, seed: Seed
) -> None:
    broadcaster = ResponseBroadcaster(
        uow=lambda: unit_of_work(factory),
        gateway=gateway,
        clock=FakeClock(FIXED_NOW),
        sleeper=FakeSleeper(),
        names_per_option=10,
        max_attempts=0,
    )
    result = await broadcaster.refresh(seed.instance_id)
    assert result.failed == 1
    assert gateway.edits == []


async def test_deleted_instance_renders_redacted(
    broadcaster: ResponseBroadcaster,
    gateway: FakeMessageGateway,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from telegram_auto_poll.db.repositories.poll_instances import PollInstanceRepository
    from telegram_auto_poll.domain.enums import InstanceStatus

    async with session_scope(factory) as session:
        await PollInstanceRepository(session).set_status(
            seed.instance_id, InstanceStatus.DELETED, now=FIXED_NOW
        )
    await broadcaster.refresh(seed.instance_id)
    assert gateway.edits[0]["text"] == "This poll was deleted."


async def test_run_without_debounce_processes_dirty(
    factory: async_sessionmaker[AsyncSession], gateway: FakeMessageGateway, seed: Seed
) -> None:
    class SignalingBroadcaster(ResponseBroadcaster):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)  # type: ignore[arg-type]
            self.processed = asyncio.Event()

        async def process_pending(self) -> object:
            result = await super().process_pending()
            self.processed.set()
            return result

    broadcaster = SignalingBroadcaster(
        uow=lambda: unit_of_work(factory),
        gateway=gateway,
        clock=FakeClock(FIXED_NOW),
        sleeper=FakeSleeper(),
        names_per_option=10,
        debounce_seconds=0,
    )
    broadcaster.mark_dirty(seed.instance_id)
    stop = asyncio.Event()
    task = asyncio.create_task(broadcaster.run(stop))
    await asyncio.wait_for(broadcaster.processed.wait(), timeout=1)
    stop.set()
    await task
    assert len(gateway.edits) == 1
