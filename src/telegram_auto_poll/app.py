"""Process guards and the supervised runtime (SPEC-003 §13).

The single-instance lock and the component supervisor live here. Startup order is health, polling,
retention cron, then the posting worker and response broadcaster. Shutdown reverses that order and
bounds the drain with ``SHUTDOWN_GRACE_SECONDS``.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Protocol

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from telegram import Bot
from telegram.ext import Application

from .bot.application import BotResponder, build_application
from .bot.auth import AdminGuard
from .bot.handlers.commands import HandlerContext
from .bot.publish_store import PublishStore
from .bot.telegram_gateway import TelegramChatGateway, TelegramMessageGateway
from .bot.wizards.store import WizardStore
from .clock import AsyncioSleeper, SystemClock
from .config import ConfigurationError, Settings, get_settings
from .db.engine import check_database, create_engine, create_session_factory
from .db.migrate import migrate_on_startup
from .db.uow import unit_of_work
from .domain.ports import Clock
from .observability.health import HealthService, create_health_app
from .observability.metrics import Metrics
from .scheduler.engine import (
    RETENTION_HOUR_UTC,
    RETENTION_JOB_ID,
    RETENTION_MINUTE_UTC,
    ApschedulerBackend,
    SchedulerHeartbeat,
)
from .services.broadcaster import ResponseBroadcaster
from .services.chat_service import ChatService
from .services.pagination import PAGE_SIZE
from .services.poll_service import PollService
from .services.posting_worker import PostingWorker
from .services.publish_service import PublishService
from .services.rate_limiter import RateLimiter
from .services.recovery_service import RecoveryService
from .services.response_service import ResponseService
from .services.retention_service import RetentionService
from .services.status_service import StatusService

logger = logging.getLogger(__name__)

LOCK_FILENAME = "app.lock"
LOCK_FILE_MODE = 0o600

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_LOCKED = 3


class LockHeldError(RuntimeError):
    """Another process already owns the data directory."""


class SingleInstanceLock:
    """An exclusive ``flock`` on ``<DATA_DIR>/app.lock``, released by the kernel on crash."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / LOCK_FILENAME
        self._descriptor: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> None:
        """Take the lock or raise :class:`LockHeldError` without blocking."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, LOCK_FILE_MODE)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            raise LockHeldError(f"another instance holds {self._path}") from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        self._descriptor = descriptor

    def release(self) -> None:
        """Release the lock if held. Safe to call twice."""
        if self._descriptor is None:
            return
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)
        self._descriptor = None

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


class PollingRunner(Protocol):
    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


class HealthRunner(Protocol):
    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


class PtbPollingRunner:
    """Start and stop long polling on an already-built PTB application."""

    def __init__(self, application: Application) -> None:  # type: ignore[type-arg]
        self._application = application

    async def start(self) -> None:
        await self._application.initialize()
        await self._application.start()
        if self._application.updater is not None:
            await self._application.updater.start_polling()

    async def stop(self) -> None:
        updater = self._application.updater
        if updater is not None and updater.running:
            await updater.stop()
        if self._application.running:
            await self._application.stop()
        await self._application.shutdown()


class AiohttpHealthRunner:
    """Bind the sidecar to the configured interface and clean it up on stop."""

    def __init__(self, application: web.Application, *, host: str, port: int) -> None:
        self._application = application
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        self._runner = web.AppRunner(self._application)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._host, self._port).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


@dataclass(slots=True)
class Runtime:
    """Owns the background components and their ordered lifecycle."""

    scheduler: ApschedulerBackend
    worker: PostingWorker
    broadcaster: ResponseBroadcaster
    polling: PollingRunner
    health: HealthRunner
    shutdown_grace_seconds: float = 30.0
    on_start: Callable[[], Awaitable[None]] | None = None
    on_stop: Callable[[], Awaitable[None]] | None = None
    _tasks: list[asyncio.Task[None]] = field(default_factory=list, init=False)

    async def run(self, stop: asyncio.Event) -> None:
        """Start everything, wait for ``stop``, then shut down in reverse order."""
        if self.on_start is not None:
            await self.on_start()
        await self.health.start()
        await self.polling.start()
        self.scheduler.start()
        logger.info("startup_complete")

        self._tasks = [
            asyncio.create_task(self.worker.run(stop)),
            asyncio.create_task(self.broadcaster.run(stop)),
        ]
        try:
            await stop.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Stop accepting work, drain within the grace period, then release resources."""
        logger.info("shutdown_started")
        await self.polling.stop()
        self.scheduler.shutdown()
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks), timeout=self.shutdown_grace_seconds
                )
            except TimeoutError:
                for task in self._tasks:
                    task.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.health.stop()
        if self.on_stop is not None:
            await self.on_stop()
        logger.info("shutdown_complete")


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Stop cleanly on SIGTERM and SIGINT."""
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)


class _BotNotifier:
    """Admin alerts through the Bot API; failures already carry sanitized text."""

    def __init__(self, bot: Bot, admin_user_ids: frozenset[int]) -> None:
        self._bot = bot
        self._admins = admin_user_ids

    async def notify_admins(self, text: str) -> None:
        for admin in sorted(self._admins):
            try:
                await self._bot.send_message(chat_id=admin, text=text)
            except Exception:
                logger.exception("admin alert failed")


async def _checkpoint_wal(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))


def _database_path(settings: Settings) -> Path:
    return Path(settings.database_url.removeprefix("sqlite+aiosqlite:///"))


def build_runtime(
    settings: Settings,
    *,
    clock: Clock | None = None,
    polling: PollingRunner | None = None,
    health: HealthRunner | None = None,
) -> Runtime:
    """Wire the production object graph. Network-facing runners can be replaced in tests."""
    resolved_clock = clock or SystemClock()
    engine = create_engine(settings.database_url, busy_timeout_ms=settings.db_busy_timeout_ms)
    factory = create_session_factory(engine)
    uow = lambda: unit_of_work(factory)  # noqa: E731

    bot = Bot(settings.telegram_bot_token.get_secret_value())
    notifier = _BotNotifier(bot, settings.admin_user_ids)
    gateway = TelegramMessageGateway(bot)
    chat_gateway = TelegramChatGateway(bot)
    metrics = Metrics(version=settings.build_version, sha=settings.build_sha)
    heartbeat = SchedulerHeartbeat()
    sleeper = AsyncioSleeper()

    def on_tick() -> None:
        heartbeat.beat()
        metrics.observe_heartbeat(heartbeat.age())

    broadcaster = ResponseBroadcaster(
        uow=uow,
        gateway=gateway,
        clock=resolved_clock,
        sleeper=sleeper,
        names_per_option=settings.response_names_per_option,
        debounce_seconds=settings.response_update_debounce_seconds,
        concurrency=settings.message_edit_concurrency,
        max_attempts=settings.message_edit_retry_attempts,
        retry_base_seconds=settings.send_retry_base_seconds,
    )
    posting = PostingWorker(
        uow=uow,
        gateway=gateway,
        pacer=RateLimiter(clock=resolved_clock, sleeper=sleeper),
        notifier=notifier,
        clock=resolved_clock,
        sleeper=sleeper.sleep,
        names_per_option=settings.response_names_per_option,
        max_attempts=settings.send_retry_attempts,
        retry_base_seconds=settings.send_retry_base_seconds,
        concurrency=settings.send_concurrency,
        tick_seconds=settings.posting_tick_seconds,
        observer=metrics,
        on_tick=on_tick,
    )
    polls = PollService(uow=uow, clock=resolved_clock, broadcaster=broadcaster)
    publish = PublishService(uow=uow, clock=resolved_clock, wake=posting.wake)
    responses = ResponseService(uow=uow, clock=resolved_clock, broadcaster=broadcaster)
    status = StatusService(uow=uow)
    chats = ChatService(
        uow=uow,
        gateway=chat_gateway,
        clock=resolved_clock,
        bot_user_id=int(settings.telegram_bot_token.get_secret_value().split(":", 1)[0]),
        page_size=PAGE_SIZE,
    )

    if polling is None:
        context = HandlerContext(
            guard=AdminGuard(
                admin_user_ids=settings.admin_user_ids,
                clock=resolved_clock,
                commands_per_minute=settings.admin_commands_per_minute,
                votes_per_minute=settings.votes_per_minute,
            ),
            chats=chats,
            polls=polls,
            publish=publish,
            responses=responses,
            status=status,
            uow=uow,
            wizards=WizardStore(clock=resolved_clock, ttl_seconds=settings.wizard_ttl_seconds),
            publish_sessions=PublishStore(
                clock=resolved_clock, ttl_seconds=settings.wizard_ttl_seconds
            ),
            clock=resolved_clock,
            responder=BotResponder(bot),
            posting=posting,
            version=settings.build_version,
            build_sha=settings.build_sha,
            started_at=resolved_clock.now(),
            heartbeat_age=heartbeat.age,
            db_check=lambda: check_database(factory),
        )
        polling = PtbPollingRunner(
            build_application(token=settings.telegram_bot_token.get_secret_value(), ctx=context)
        )

    health_service = HealthService(
        database=lambda: check_database(factory),
        heartbeat=heartbeat,
        tick_seconds=settings.posting_tick_seconds,
    )
    if health is None:
        health = AiohttpHealthRunner(
            create_health_app(health=health_service, metrics=metrics),
            host=settings.health_host,
            port=settings.health_port,
        )

    retention = RetentionService(
        uow=uow,
        clock=resolved_clock,
        checkpoint=lambda: _checkpoint_wal(engine),
        redact=broadcaster.redact,
        response_retention_days=settings.response_retention_days,
        retention_days=settings.retention_days,
    )
    scheduler = ApschedulerBackend()

    async def run_retention() -> None:
        result = await retention.run()
        metrics.retention_deleted.labels(table="poll_responses").inc(result.responses)
        metrics.retention_deleted.labels(table="poll_messages").inc(result.messages)
        metrics.retention_deleted.labels(table="audit_events").inc(result.audit_events)
        logger.info(
            "retention_completed",
            extra={
                "responses": result.responses,
                "messages": result.messages,
                "audit_events": result.audit_events,
            },
        )

    scheduler.add_cron(
        RETENTION_JOB_ID,
        hour=RETENTION_HOUR_UTC,
        minute=RETENTION_MINUTE_UTC,
        callback=run_retention,
    )

    recovery = RecoveryService(
        uow=uow,
        clock=resolved_clock,
        notifier=notifier,
    )

    async def on_start() -> None:
        await migrate_on_startup(
            engine=engine,
            db_path=_database_path(settings),
            backup_dir=settings.data_dir / "backups",
            now=resolved_clock.now(),
            allow_destructive=settings.allow_destructive_migration,
        )
        await recovery.run()
        metrics.mark_started(resolved_clock.now().timestamp())

    return Runtime(
        scheduler=scheduler,
        worker=posting,
        broadcaster=broadcaster,
        polling=polling,
        health=health,
        shutdown_grace_seconds=settings.shutdown_grace_seconds,
        on_start=on_start,
        on_stop=engine.dispose,
    )


def main(settings: Settings | None = None) -> int:
    """Load configuration, take the lock, and start. Returns the process exit code."""
    try:
        resolved = get_settings() if settings is None else settings
    except ConfigurationError as exc:
        print(f"telegram-auto-poll: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    lock = SingleInstanceLock(resolved.data_dir)
    try:
        lock.acquire()
    except LockHeldError as exc:
        print(f"telegram-auto-poll: refusing to start: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    try:
        asyncio.set_event_loop(loop)
        runtime = build_runtime(resolved)
        _install_signal_handlers(loop, stop)
        loop.run_until_complete(runtime.run(stop))
        return EXIT_OK
    except Exception:
        logger.exception("fatal startup error")
        return EXIT_FAILURE
    finally:
        loop.close()
        lock.release()
