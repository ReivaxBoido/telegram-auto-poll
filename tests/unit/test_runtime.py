"""Runtime supervision and signal handling (T-037, FR-126)."""

from __future__ import annotations

import asyncio
import signal

import pytest

from telegram_auto_poll.app import Runtime, _install_signal_handlers


class Recorder:
    def __init__(self, events: list[str]) -> None:
        self.events = events


class FakeRunner(Recorder):
    def __init__(self, events: list[str], name: str) -> None:
        super().__init__(events)
        self.name = name

    async def start(self) -> None:
        self.events.append(f"{self.name}.start")

    async def stop(self) -> None:
        self.events.append(f"{self.name}.stop")


class FakeScheduler(FakeRunner):
    async def shutdown(self) -> None:
        self.events.append("scheduler.shutdown")


class FakeWorker(Recorder):
    def __init__(self, events: list[str], *, started: asyncio.Event) -> None:
        super().__init__(events)
        self.started = started
        self.saw_stop = False

    async def run(self, stop: asyncio.Event) -> None:
        self.events.append("worker.run")
        self.started.set()
        await stop.wait()
        self.saw_stop = True


class SlowWorker(Recorder):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)

    async def run(self, stop: asyncio.Event) -> None:
        self.events.append("worker.run")
        await asyncio.Event().wait()


def build(
    events: list[str],
    *,
    worker: object | None = None,
    started: asyncio.Event | None = None,
    on_start: object = None,
    on_stop: object = None,
) -> tuple[Runtime, asyncio.Event, object]:
    stop = asyncio.Event()
    worker = worker or FakeWorker(events, started=started or asyncio.Event())
    runtime = Runtime(
        scheduler=FakeScheduler(events, "scheduler"),
        worker=worker,
        polling=FakeRunner(events, "polling"),
        health=FakeRunner(events, "health"),
        shutdown_grace_seconds=0.05,
        on_start=on_start,  # type: ignore[arg-type]
        on_stop=on_stop,  # type: ignore[arg-type]
    )
    return runtime, stop, worker


async def test_components_start_and_stop_in_order() -> None:
    events: list[str] = []
    started = asyncio.Event()
    runtime, stop, worker = build(events, started=started)

    task = asyncio.create_task(runtime.run(stop))
    await asyncio.wait_for(started.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert events == [
        "health.start",
        "polling.start",
        "scheduler.start",
        "worker.run",
        "polling.stop",
        "scheduler.shutdown",
        "health.stop",
    ]
    assert worker.saw_stop is True  # type: ignore[attr-defined]


async def test_start_and_stop_hooks_run_around_the_components() -> None:
    events: list[str] = []
    started = asyncio.Event()

    async def on_start() -> None:
        events.append("on_start")

    async def on_stop() -> None:
        events.append("on_stop")

    runtime, stop, _ = build(events, started=started, on_start=on_start, on_stop=on_stop)
    task = asyncio.create_task(runtime.run(stop))
    await asyncio.wait_for(started.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert events[0] == "on_start"
    assert events[-1] == "on_stop"


async def test_a_worker_that_misses_the_grace_period_is_cancelled() -> None:
    events: list[str] = []
    runtime, stop, _ = build(events, worker=SlowWorker(events))

    task = asyncio.create_task(runtime.run(stop))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert "health.stop" in events
    assert runtime._worker_task is not None
    assert runtime._worker_task.cancelled()


async def test_sigterm_and_sigint_set_the_stop_event() -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        stop = asyncio.Event()
        _install_signal_handlers(loop, stop)
        try:
            signal.raise_signal(signum)
            await asyncio.sleep(0.05)
            assert stop.is_set() is True
        finally:
            loop.remove_signal_handler(signum)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
async def test_install_covers_both_signals(signum: int) -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    _install_signal_handlers(loop, stop)
    try:
        signal.raise_signal(signum)
        await asyncio.sleep(0.05)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        loop.remove_signal_handler(signal.SIGINT)
    assert stop.is_set() is True
