"""Runtime wiring and lifecycle (SPEC-003 §13)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web

from telegram_auto_poll.app import (
    AiohttpHealthRunner,
    PtbPollingRunner,
    Runtime,
    _BotNotifier,
    build_runtime,
)
from telegram_auto_poll.config import load_settings
from tests.conftest import FAKE_BOT_TOKEN


class FakePolling:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


class FakeHealth:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


def _settings(tmp_path: Path) -> object:
    return load_settings(
        {
            "TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN,
            "ADMIN_USER_IDS": "100000001",
            "DATA_DIR": str(tmp_path / "data"),
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}",
        }
    )


async def test_build_runtime_and_run_lifecycle(tmp_path: Path) -> None:
    polling = FakePolling()
    health = FakeHealth()
    runtime = build_runtime(_settings(tmp_path), polling=polling, health=health)  # type: ignore[arg-type]
    stop = asyncio.Event()
    stop.set()

    await runtime.run(stop)

    assert polling.started == 1
    assert polling.stopped == 1
    assert health.started == 1
    assert health.stopped == 1


async def test_runtime_shutdown_is_idempotent_without_tasks() -> None:
    polling = FakePolling()
    health = FakeHealth()
    runtime = Runtime(
        scheduler=SimpleNamespace(start=lambda: None, shutdown=lambda: None),  # type: ignore[arg-type]
        worker=SimpleNamespace(run=lambda stop: asyncio.sleep(0)),  # type: ignore[arg-type]
        broadcaster=SimpleNamespace(run=lambda stop: asyncio.sleep(0)),  # type: ignore[arg-type]
        polling=polling,
        health=health,
    )
    await runtime.shutdown()
    assert polling.stopped == 1


async def test_bot_notifier_alerts_every_admin_and_swallows_failures() -> None:
    class Bot:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def send_message(self, chat_id: int, text: str) -> None:
            self.calls.append(chat_id)
            if chat_id == 2:
                raise RuntimeError("blocked")

    bot = Bot()
    notifier = _BotNotifier(bot, frozenset({1, 2}))  # type: ignore[arg-type]
    await notifier.notify_admins("alert")
    assert bot.calls == [1, 2]


async def test_ptb_polling_runner_start_and_stop() -> None:
    class Updater:
        def __init__(self) -> None:
            self.running = False

        async def start_polling(self) -> None:
            self.running = True

        async def stop(self) -> None:
            self.running = False

    class Application:
        def __init__(self) -> None:
            self.updater = Updater()
            self.running = False

        async def initialize(self) -> None:
            pass

        async def start(self) -> None:
            self.running = True

        async def stop(self) -> None:
            self.running = False

        async def shutdown(self) -> None:
            pass

    application = Application()
    runner = PtbPollingRunner(application)  # type: ignore[arg-type]
    await runner.start()
    assert application.running is True
    await runner.stop()
    assert application.running is False


async def test_aiohttp_health_runner_binds_and_cleans_up() -> None:
    runner = AiohttpHealthRunner(web.Application(), host="127.0.0.1", port=0)
    await runner.start()
    await runner.stop()


async def test_ptb_polling_runner_without_updater_or_running_app() -> None:
    class Application:
        updater = None
        running = False

        async def initialize(self) -> None:
            pass

        async def start(self) -> None:
            self.running = True

        async def stop(self) -> None:
            self.running = False

        async def shutdown(self) -> None:
            pass

    runner = PtbPollingRunner(Application())  # type: ignore[arg-type]
    await runner.start()
    await runner.stop()


async def test_aiohttp_health_runner_stop_before_start() -> None:
    runner = AiohttpHealthRunner(web.Application(), host="127.0.0.1", port=0)
    await runner.stop()


async def test_runtime_timeout_cancels_tasks() -> None:
    async def never(stop: asyncio.Event) -> None:
        await asyncio.sleep(3600)

    runtime = Runtime(
        scheduler=SimpleNamespace(start=lambda: None, shutdown=lambda: None),  # type: ignore[arg-type]
        worker=SimpleNamespace(run=never),  # type: ignore[arg-type]
        broadcaster=SimpleNamespace(run=never),  # type: ignore[arg-type]
        polling=FakePolling(),
        health=FakeHealth(),
        shutdown_grace_seconds=0.01,
    )
    stop = asyncio.Event()
    runtime._tasks = [asyncio.create_task(never(stop)), asyncio.create_task(never(stop))]
    await runtime.shutdown()
    assert all(task.cancelled() for task in runtime._tasks)


async def test_checkpoint_wal_runs(tmp_path: Path) -> None:
    from telegram_auto_poll.app import _checkpoint_wal
    from telegram_auto_poll.db.engine import create_engine

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'checkpoint.db'}")
    await _checkpoint_wal(engine)
    await engine.dispose()


async def test_build_runtime_without_overrides_and_retention_callback(tmp_path: Path) -> None:
    from telegram_auto_poll.app import build_runtime
    from telegram_auto_poll.scheduler.engine import RETENTION_JOB_ID

    runtime = build_runtime(_settings(tmp_path))  # type: ignore[arg-type]
    await runtime.on_start()  # type: ignore[misc]
    await runtime.worker.tick()
    job = runtime.scheduler._scheduler.get_job(RETENTION_JOB_ID)
    assert job is not None
    await job.func()
    await runtime.on_stop()  # type: ignore[misc]


def test_main_returns_failure_on_a_fatal_startup_error(tmp_path: Path, monkeypatch: Any) -> None:
    from telegram_auto_poll import app as app_module

    def boom(settings: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "build_runtime", boom)
    assert app_module.main(_settings(tmp_path)) == 1  # type: ignore[arg-type]


async def test_ptb_polling_runner_stop_without_start() -> None:
    class Application:
        updater = None
        running = False

        async def shutdown(self) -> None:
            pass

    await PtbPollingRunner(Application()).stop()  # type: ignore[arg-type]


async def test_runtime_run_without_on_start() -> None:
    polling = FakePolling()
    health = FakeHealth()

    async def done(stop: asyncio.Event) -> None:
        return None

    runtime = Runtime(
        scheduler=SimpleNamespace(start=lambda: None, shutdown=lambda: None),  # type: ignore[arg-type]
        worker=SimpleNamespace(run=done),  # type: ignore[arg-type]
        broadcaster=SimpleNamespace(run=done),  # type: ignore[arg-type]
        polling=polling,
        health=health,
    )
    stop = asyncio.Event()
    stop.set()
    await runtime.run(stop)
    assert polling.started == 1


def test_main_configures_logging(tmp_path: Path, monkeypatch: Any) -> None:
    from telegram_auto_poll import app as app_module

    called: dict[str, object] = {}

    def capture(**kwargs: object) -> None:
        called.update(kwargs)

    async def run(stop: asyncio.Event) -> None:
        return None

    monkeypatch.setattr(app_module, "configure_logging", capture)
    monkeypatch.setattr(app_module, "build_runtime", lambda settings: SimpleNamespace(run=run))
    assert app_module.main(_settings(tmp_path)) == 0  # type: ignore[arg-type]
    assert called["log_format"] == "json"
    assert called["environment"] == "production"
