"""Delivery worker behaviour (T-034): bounded concurrency, crash restarts, clean stop."""

from __future__ import annotations

import asyncio
import logging

import pytest

from telegram_auto_poll.domain.enums import DeliveryStatus
from telegram_auto_poll.scheduler.worker import DeliveryWorker
from telegram_auto_poll.services.delivery_service import SendOutcome


class ScriptedService:
    """A dispatch script: each call consumes one response (a list or an exception)."""

    def __init__(self, responses: list[object], *, stop: asyncio.Event | None = None) -> None:
        self.responses = responses
        self.calls = 0
        self.limits: list[int] = []
        self.stop = stop

    async def dispatch(self, *, limit: int) -> list[SendOutcome]:
        self.calls += 1
        self.limits.append(limit)
        response = self.responses.pop(0) if self.responses else []
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, list)
        return response


def worker(service: ScriptedService, **kwargs: float | int) -> DeliveryWorker:
    return DeliveryWorker(service=service, **kwargs)  # type: ignore[arg-type]


async def test_each_loop_claims_one_row_at_a_time() -> None:
    stop = asyncio.Event()
    service = ScriptedService([[], [], []], stop=stop)
    calls_seen = 0

    original = service.dispatch

    async def dispatch(*, limit: int) -> list[SendOutcome]:
        nonlocal calls_seen
        calls_seen += 1
        if calls_seen == 3:
            stop.set()
        return await original(limit=limit)

    service.dispatch = dispatch  # type: ignore[method-assign]

    await worker(service, concurrency=3, idle_seconds=0).run(stop)

    assert service.limits == [1, 1, 1]


async def test_a_pre_set_stop_event_prevents_any_claim() -> None:
    stop = asyncio.Event()
    stop.set()
    service = ScriptedService([])

    await worker(service).run(stop)

    assert service.calls == 0


async def test_an_idle_loop_waits_for_the_stop_event() -> None:
    stop = asyncio.Event()
    service = ScriptedService([[], [], []])
    service.stop = stop

    async def dispatch(*, limit: int) -> list[SendOutcome]:
        result = await ScriptedService.dispatch(service, limit=limit)
        if service.calls >= 3:
            stop.set()
        return result

    service.dispatch = dispatch  # type: ignore[method-assign]

    await worker(service, concurrency=1, idle_seconds=0).run(stop)

    assert service.calls == 3


async def test_a_crash_is_logged_and_the_loop_restarts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stop = asyncio.Event()
    service = ScriptedService([RuntimeError("boom"), []], stop=stop)

    original = service.dispatch

    async def dispatch(*, limit: int) -> list[SendOutcome]:
        result = await original(limit=limit)
        if service.calls >= 2:
            stop.set()
        return result

    service.dispatch = dispatch  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        await worker(service, concurrency=1, idle_seconds=0, error_backoff_seconds=0).run(stop)

    assert service.calls == 2
    assert "delivery worker failed; restarting" in caplog.text


async def test_an_in_flight_send_finishes_before_run_returns() -> None:
    stop = asyncio.Event()
    started = asyncio.Event()
    release = asyncio.Event()
    completed: list[SendOutcome] = []

    class BlockingService:
        async def dispatch(self, *, limit: int) -> list[SendOutcome]:
            started.set()
            await release.wait()
            outcome = SendOutcome(delivery_id=1, status=DeliveryStatus.SENT, attempts=1)
            completed.append(outcome)
            return [outcome]

    subject = DeliveryWorker(
        service=BlockingService(),  # type: ignore[arg-type]
        concurrency=1,
        idle_seconds=0,
    )
    task = asyncio.create_task(subject.run(stop))
    await asyncio.wait_for(started.wait(), timeout=1)

    # Shutdown arrives while the send is in flight: the loop must not abandon it mid-request.
    stop.set()
    release.set()
    await asyncio.wait_for(task, timeout=1)

    assert len(completed) == 1
    assert task.done()


async def test_run_leaves_no_tasks_behind() -> None:
    stop = asyncio.Event()
    stop.set()
    before = len(asyncio.all_tasks())

    await worker(ScriptedService([]), concurrency=2).run(stop)

    assert len(asyncio.all_tasks()) == before


async def test_cancellation_propagates_out_of_the_loop() -> None:
    stop = asyncio.Event()
    started = asyncio.Event()

    class BlockingService:
        async def dispatch(self, *, limit: int) -> list[SendOutcome]:
            started.set()
            await asyncio.Event().wait()
            return []

    subject = DeliveryWorker(
        service=BlockingService(),  # type: ignore[arg-type]
        concurrency=1,
    )
    task = asyncio.create_task(subject.run(stop))
    await asyncio.wait_for(started.wait(), timeout=1)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
