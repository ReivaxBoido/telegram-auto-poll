"""Posting worker: send pending poll messages with retries (SPEC-003 FR-331 - FR-335).

The worker claims rows in one short transaction, performs Telegram I/O outside it, and records the
outcome in another transaction. A crash between the send and the record is ambiguous and is handled by
startup recovery (``sending`` -> ``failed``), never by an automatic resend.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Protocol

from ..db.uow import Repositories, UnitOfWork
from ..domain.entities import OptionResponses, Poll, PollInstance, PollMessage
from ..domain.enums import InstanceStatus, PollStatus, SkipReason
from ..domain.errors import DeliveryError
from ..domain.poll_render import keyboard_rows, render_poll_message
from ..domain.ports import AdminNotifier, Clock, MessageGateway, Pacer

logger = logging.getLogger(__name__)

DEFAULT_TICK_SECONDS = 5
DEFAULT_BATCH_SIZE = 10
MAX_RETRY_AFTER_SECONDS = 300.0
AMBIGUOUS_OUTCOME_REASON = "ambiguous_outcome_after_restart"


class PostingObserver(Protocol):
    """Metrics seam; the app supplies a Prometheus-backed implementation."""

    def sent(self) -> None:
        raise NotImplementedError

    def failed(self, reason: str) -> None:
        raise NotImplementedError

    def skipped(self) -> None:
        raise NotImplementedError


class _NullObserver:
    def sent(self) -> None:
        pass

    def failed(self, reason: str) -> None:
        pass

    def skipped(self) -> None:
        pass


class PostingWorker:
    """Claims and sends pending poll messages."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        gateway: MessageGateway,
        pacer: Pacer,
        notifier: AdminNotifier,
        clock: Clock,
        sleeper: Callable[[float], Awaitable[None]],
        names_per_option: int,
        max_attempts: int,
        retry_base_seconds: float,
        concurrency: int,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        observer: PostingObserver | None = None,
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        self._uow = uow
        self._gateway = gateway
        self._pacer = pacer
        self._notifier = notifier
        self._clock = clock
        self._sleeper = sleeper
        self._names_per_option = names_per_option
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._semaphore = asyncio.Semaphore(concurrency)
        self._tick_seconds = tick_seconds
        self._batch_size = batch_size
        self._observer = observer or _NullObserver()
        self._on_tick = on_tick
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Ask the loop to run now."""
        self._wake.set()

    async def run(self, stop: asyncio.Event) -> None:
        """Send pending work until ``stop`` is set."""
        while not stop.is_set():
            await self.tick()
            waiter = asyncio.create_task(self._wake.wait())
            stopper = asyncio.create_task(stop.wait())
            _done, pending = await asyncio.wait(
                {waiter, stopper}, timeout=self._tick_seconds, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in pending:
                await asyncio.gather(task, return_exceptions=True)
            self._wake.clear()

    async def tick(self) -> int:
        """Claim one batch and send it with bounded concurrency."""
        if self._on_tick is not None:
            self._on_tick()
        claimed = await self._claim()
        if not claimed:
            return 0
        await asyncio.gather(*(self._send_one(message) for message in claimed))
        return len(claimed)

    async def _claim(self) -> list[PollMessage]:
        async with self._uow() as repos:
            return await repos.messages.claim_pending(now=self._clock.now(), limit=self._batch_size)

    async def _send_one(self, message: PollMessage) -> None:
        async with self._semaphore:
            loaded = await self._load(message)
            if loaded is None:
                return
            poll, instance, chat_id, telegram_chat_id = loaded
            try:
                render = render_poll_message(
                    poll,
                    instance,
                    tuple(OptionResponses(option=option) for option in poll.options),
                    names_per_option=self._names_per_option,
                )
            except ValueError:
                await self._skip(message, SkipReason.POLL_MISSING)
                return

            await self._pacer.acquire(telegram_chat_id)
            try:
                telegram_message_id = await self._gateway.send_message(
                    telegram_chat_id,
                    render.text,
                    keyboard=keyboard_rows(render),
                )
            except DeliveryError as exc:
                await self._handle_failure(message, exc)
                return

            async with self._uow() as repos:
                await repos.messages.mark_sent(
                    message.id or 0,
                    telegram_message_id,
                    now=self._clock.now(),
                )
            self._observer.sent()
            logger.info(
                "poll_message_sent",
                extra={
                    "message_id": message.id,
                    "instance_id": message.instance_id,
                    "chat_id": chat_id,
                },
            )

    async def _load(self, message: PollMessage) -> tuple[Poll, PollInstance, int, int] | None:
        async with self._uow() as repos:
            instance = await repos.instances.get(message.instance_id)
            if instance is None:
                await self._skip_in_session(repos, message, SkipReason.POLL_MISSING)
                return None
            poll = await repos.polls.get(instance.poll_id)
            if poll is None:
                await self._skip_in_session(repos, message, SkipReason.POLL_MISSING)
                return None
            if poll.status is PollStatus.DELETED:
                await self._skip_in_session(repos, message, SkipReason.POLL_DELETED)
                return None
            if instance.status is not InstanceStatus.OPEN:
                reason = (
                    SkipReason.INSTANCE_DELETED
                    if instance.status is InstanceStatus.DELETED
                    else SkipReason.INSTANCE_CLOSED
                )
                await self._skip_in_session(repos, message, reason)
                return None
            if message.chat_id is None:
                await self._skip_in_session(repos, message, SkipReason.CHAT_INACTIVE)
                return None
            chat = await repos.chats.get(message.chat_id)
            if chat is None or not chat.is_active:
                await self._skip_in_session(repos, message, SkipReason.CHAT_INACTIVE)
                return None
            return poll, instance, chat.id or 0, chat.telegram_chat_id

    async def _skip_in_session(
        self,
        repos: Repositories,
        message: PollMessage,
        reason: SkipReason,
    ) -> None:
        await repos.messages.mark_skipped(
            message.id or 0,
            reason.value,
            now=self._clock.now(),
        )
        self._observer.skipped()

    async def _skip(self, message: PollMessage, reason: SkipReason) -> None:
        async with self._uow() as repos:
            await repos.messages.mark_skipped(
                message.id or 0,
                reason.value,
                now=self._clock.now(),
            )
        self._observer.skipped()

    async def _handle_failure(self, message: PollMessage, error: DeliveryError) -> None:
        if error.retryable and message.attempt_count < self._max_attempts:
            next_attempt = self._clock.now() + timedelta(
                seconds=self._backoff(message.attempt_count, error.retry_after)
            )
            async with self._uow() as repos:
                await repos.messages.reset_for_retry(
                    message.id or 0,
                    next_attempt_at=next_attempt,
                    now=self._clock.now(),
                )
            return

        async with self._uow() as repos:
            await repos.messages.mark_failed(
                message.id or 0,
                error.classification,
                now=self._clock.now(),
            )
        self._observer.failed(error.classification)
        logger.error(
            "poll_message_failed",
            extra={
                "message_id": message.id,
                "instance_id": message.instance_id,
                "attempt_count": message.attempt_count,
                "classification": error.classification,
            },
        )
        await self._notifier.notify_admins(
            "poll message failed: "
            f"message #{message.id}, instance #{message.instance_id}, "
            f"attempts {message.attempt_count}, reason {error.classification}"
        )

    def _backoff(self, attempt_count: int, retry_after: float | None) -> float:
        base: float = self._retry_base_seconds * (2 ** max(attempt_count - 1, 0))
        if retry_after is not None:
            base = max(base, min(retry_after, MAX_RETRY_AFTER_SECONDS))
        return base


async def fail_stale_sending(uow: UnitOfWork, clock: Clock, notifier: AdminNotifier) -> int:
    """Startup recovery: fail rows left in ``sending`` by a crash, then alert once."""
    async with uow() as repos:
        failed = await repos.messages.fail_stale_sending(AMBIGUOUS_OUTCOME_REASON, now=clock.now())
    if failed:
        await notifier.notify_admins(
            f"startup recovery failed {failed} poll message(s) with an unknown outcome "
            f"({AMBIGUOUS_OUTCOME_REASON}); they will not be retried"
        )
    return failed


# ``fail_stale_sending`` is the startup recovery entry point used by the composition root.
__all__ = [
    "AMBIGUOUS_OUTCOME_REASON",
    "PostingObserver",
    "PostingWorker",
    "fail_stale_sending",
]
