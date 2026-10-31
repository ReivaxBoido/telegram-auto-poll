"""Production wiring and runners (T-037)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_auto_poll.app import (
    AiohttpHealthRunner,
    PtbPollingRunner,
    _BotNotifier,
    build_runtime,
)
from telegram_auto_poll.config import load_settings
from telegram_auto_poll.db.engine import create_engine
from telegram_auto_poll.db.migrate import current_revision
from telegram_auto_poll.observability.health import create_health_app
from telegram_auto_poll.observability.metrics import Metrics
from tests.conftest import FAKE_BOT_TOKEN
from tests.factories import FIXED_NOW
from tests.fakes import FakeClock


class FakeRunner:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


def settings(tmp_path: Path) -> object:
    return load_settings(
        {
            "TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN,
            "ADMIN_USER_IDS": "100000001",
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}",
            "DATA_DIR": str(tmp_path),
        }
    )


async def test_build_runtime_migrates_recovers_and_schedules(tmp_path: Path) -> None:
    polling = FakeRunner()
    health = FakeRunner()
    runtime = build_runtime(
        settings(tmp_path),
        clock=FakeClock(FIXED_NOW),
        polling=polling,
        health=health,  # type: ignore[arg-type]
    )

    await runtime.on_start()  # type: ignore[misc]
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    try:
        assert await current_revision(engine) == "0001"
    finally:
        await engine.dispose()

    await runtime.scheduler.start()  # type: ignore[attr-defined]
    await runtime.scheduler.tick()  # type: ignore[attr-defined]
    await runtime.scheduler._backend._run_tick()  # type: ignore[attr-defined]
    await runtime.scheduler.shutdown()  # type: ignore[attr-defined]
    await runtime.on_stop()  # type: ignore[misc]
    assert health.started == 0


async def test_build_runtime_creates_the_production_runners(tmp_path: Path) -> None:
    runtime = build_runtime(settings(tmp_path), clock=FakeClock(FIXED_NOW))  # type: ignore[arg-type]

    assert isinstance(runtime.polling, PtbPollingRunner)
    assert isinstance(runtime.health, AiohttpHealthRunner)
    await runtime.on_stop()  # type: ignore[misc]
    await runtime.health.stop()
    await runtime.health.stop()


async def test_the_ptb_runner_starts_and_stops_polling() -> None:
    calls: list[str] = []

    class Updater:
        running = True

        async def start_polling(self) -> None:
            calls.append("poll")

        async def stop(self) -> None:
            calls.append("stop-poll")

    class Application:
        updater = Updater()
        running = True

        async def initialize(self) -> None:
            calls.append("init")

        async def start(self) -> None:
            calls.append("start")

        async def stop(self) -> None:
            calls.append("stop")

        async def shutdown(self) -> None:
            calls.append("shutdown")

    runner = PtbPollingRunner(Application())  # type: ignore[arg-type]
    await runner.start()
    await runner.stop()

    assert calls == ["init", "start", "poll", "stop-poll", "stop", "shutdown"]


async def test_the_ptb_runner_tolerates_a_disabled_updater() -> None:
    application = SimpleNamespace(
        updater=None,
        running=False,
        initialize=_async_noop,
        start=_async_noop,
        stop=_async_noop,
        shutdown=_async_noop,
    )
    runner = PtbPollingRunner(application)  # type: ignore[arg-type]

    await runner.start()
    await runner.stop()


async def _async_noop() -> None:
    return None


async def test_bot_notifier_sends_to_every_admin(tmp_path: Path) -> None:
    sent: list[tuple[int, str]] = []

    class Bot:
        async def send_message(self, chat_id: int, text: str) -> None:
            sent.append((chat_id, text))

    notifier = _BotNotifier(Bot(), frozenset({1, 2}))
    await notifier.notify_admins("alert")

    assert sent == [(1, "alert"), (2, "alert")]


async def test_bot_notifier_swallows_and_logs_a_failure() -> None:
    class Bot:
        async def send_message(self, chat_id: int, text: str) -> None:
            raise RuntimeError("forbidden")

    notifier = _BotNotifier(Bot(), frozenset({1}))
    await notifier.notify_admins("alert")


async def test_the_production_health_runner_starts_and_stops() -> None:
    from telegram_auto_poll.observability.health import HealthService
    from telegram_auto_poll.scheduler.engine import SchedulerHeartbeat

    async def ok() -> bool:
        return True

    heartbeat = SchedulerHeartbeat()
    heartbeat.beat()
    app = create_health_app(
        health=HealthService(database=ok, heartbeat=heartbeat, tick_seconds=15),
        metrics=Metrics(),
    )
    runner = AiohttpHealthRunner(app, host="127.0.0.1", port=0)

    await runner.start()
    await runner.stop()
    await runner.stop()


async def test_main_returns_failure_when_startup_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from telegram_auto_poll.app import main

    def boom(settings: object) -> object:
        raise RuntimeError("cannot start")

    monkeypatch.setattr("telegram_auto_poll.app.build_runtime", boom)

    assert main(settings(tmp_path)) == 1  # type: ignore[arg-type]


async def test_the_retention_cron_runs_daily_at_0315_utc(tmp_path: Path) -> None:
    runtime = build_runtime(settings(tmp_path), clock=FakeClock(FIXED_NOW))  # type: ignore[arg-type]
    backend = runtime.scheduler._backend  # type: ignore[attr-defined]
    job = backend._scheduler.get_job("retention-daily")

    assert job is not None
    trigger = str(job.trigger)
    assert "hour='3'" in trigger
    assert "minute='15'" in trigger
    assert "retention-daily" not in backend.jobs()

    await runtime.on_start()  # type: ignore[misc]
    await job.func()
    await job.func()
    await runtime.on_stop()  # type: ignore[misc]
