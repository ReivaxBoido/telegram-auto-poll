"""Coalesced live rendering of poll messages (SPEC-003 FR-322, FR-329, FR-330).

Votes mark an instance dirty. The broadcaster renders the instance once per debounce window and edits
every sent message for it. Edits use bounded concurrency and classified retries; a missing Telegram
message is recorded as ``missing`` and never retried automatically.

The database is authoritative. If the process restarts, messages may be stale until the next vote or
``/refresh``; the queue is deliberately in memory.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..db.uow import UnitOfWork
from ..domain.entities import (
    BroadcastResult,
    OptionResponses,
    Poll,
    PollInstance,
    PollMessage,
    PollRender,
    PollResponse,
)
from ..domain.enums import InstanceStatus, MessageStatus, PollStatus
from ..domain.errors import DeliveryError
from ..domain.poll_render import keyboard_rows, render_poll_message
from ..domain.ports import Clock, MessageGateway, Sleeper

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_SECONDS = 1.0
DEFAULT_EDIT_CONCURRENCY = 4
DEFAULT_EDIT_ATTEMPTS = 3
DEFAULT_RETRY_BASE_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class _Loaded:
    poll: Poll
    instance: PollInstance
    responses: tuple[PollResponse, ...]
    messages: tuple[PollMessage, ...]
    telegram_chat_ids: dict[int, int]


class ResponseBroadcaster:
    """Renders dirty instances and edits their messages."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        gateway: MessageGateway,
        clock: Clock,
        sleeper: Sleeper,
        names_per_option: int,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        concurrency: int = DEFAULT_EDIT_CONCURRENCY,
        max_attempts: int = DEFAULT_EDIT_ATTEMPTS,
        retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
    ) -> None:
        self._uow = uow
        self._gateway = gateway
        self._clock = clock
        self._sleeper = sleeper
        self._names_per_option = names_per_option
        self._debounce_seconds = debounce_seconds
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._dirty: set[int] = set()
        self._wake = asyncio.Event()

    def mark_dirty(self, instance_id: int) -> None:
        """Queue an instance for the next render pass."""
        self._dirty.add(instance_id)
        self._wake.set()

    async def refresh(self, instance_id: int) -> BroadcastResult:
        """Render and edit one instance immediately."""
        return await self._render_and_edit(instance_id, redact=False)

    async def redact(self, instance_ids: list[int]) -> BroadcastResult:
        """Redact every message of the given instances."""
        total = BroadcastResult()
        for instance_id in instance_ids:
            result = await self._render_and_edit(instance_id, redact=True)
            total = BroadcastResult(
                edited=total.edited + result.edited,
                missing=total.missing + result.missing,
                failed=total.failed + result.failed,
            )
        return total

    async def process_pending(self) -> BroadcastResult:
        """Process every currently dirty instance once."""
        total = BroadcastResult()
        while self._dirty:
            instance_id = self._dirty.pop()
            result = await self._render_and_edit(instance_id, redact=False)
            total = BroadcastResult(
                edited=total.edited + result.edited,
                missing=total.missing + result.missing,
                failed=total.failed + result.failed,
            )
        self._wake.clear()
        return total

    async def run(self, stop: asyncio.Event) -> None:
        """Consume dirty instances until ``stop`` is set, then flush."""
        while not stop.is_set():
            waiter = asyncio.create_task(self._wake.wait())
            stopper = asyncio.create_task(stop.wait())
            _done, pending = await asyncio.wait(
                {waiter, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in pending:
                await asyncio.gather(task, return_exceptions=True)
            if stop.is_set():
                break
            if self._debounce_seconds > 0:
                await self._sleeper.sleep(self._debounce_seconds)
            await self.process_pending()
        await self.process_pending()

    async def _render_and_edit(self, instance_id: int, *, redact: bool) -> BroadcastResult:
        loaded = await self._load(instance_id)
        if loaded is None:
            return BroadcastResult()
        render = self._render(loaded, redact=redact)
        keyboard = None if redact or render.closed else keyboard_rows(render)

        results = await asyncio.gather(
            *[
                self._edit_one(
                    message,
                    render.text,
                    keyboard,
                    loaded.telegram_chat_ids.get(message.chat_id or 0),
                )
                for message in loaded.messages
                if message.status is MessageStatus.SENT and message.telegram_message_id is not None
            ]
        )
        return BroadcastResult(
            edited=sum(1 for result in results if result == "edited"),
            missing=sum(1 for result in results if result == "missing"),
            failed=sum(1 for result in results if result == "failed"),
        )

    async def _load(self, instance_id: int) -> _Loaded | None:
        async with self._uow() as repos:
            instance = await repos.instances.get(instance_id)
            if instance is None:
                return None
            poll = await repos.polls.get(instance.poll_id)
            if poll is None:
                return None
            responses = tuple(await repos.responses.list_for_instance(instance_id))
            messages = tuple(await repos.messages.list_for_instance(instance_id))
            telegram_chat_ids: dict[int, int] = {}
            for chat_id in {message.chat_id for message in messages if message.chat_id is not None}:
                chat = await repos.chats.get(chat_id)
                if chat is not None:
                    telegram_chat_ids[chat_id] = chat.telegram_chat_id
        return _Loaded(
            poll=poll,
            instance=instance,
            responses=responses,
            messages=messages,
            telegram_chat_ids=telegram_chat_ids,
        )

    def _render(self, loaded: _Loaded, *, redact: bool) -> PollRender:
        if redact:
            return PollRender(text="This poll was deleted.", closed=True)
        if (
            loaded.poll.status is PollStatus.DELETED
            or loaded.instance.status is InstanceStatus.DELETED
        ):
            return PollRender(text="This poll was deleted.", closed=True)
        by_option: dict[int, list[tuple[int, str]]] = {}
        for response in loaded.responses:
            by_option.setdefault(response.option_id, []).append(
                (response.telegram_user_id, response.first_name)
            )
        options = tuple(
            OptionResponses(
                option=option,
                respondents=tuple(by_option.get(option.id or -1, [])),
            )
            for option in loaded.poll.options
        )
        return render_poll_message(
            loaded.poll,
            loaded.instance,
            options,
            names_per_option=self._names_per_option,
        )

    async def _edit_one(
        self,
        message: PollMessage,
        text: str,
        keyboard: tuple[tuple[tuple[str, str], ...], ...] | None,
        telegram_chat_id: int | None,
    ) -> str:
        async with self._semaphore:
            for attempt in range(self._max_attempts):
                try:
                    await self._gateway.edit_message(
                        text=text,
                        chat_id=telegram_chat_id,
                        message_id=message.telegram_message_id,
                        inline_message_id=message.inline_message_id,
                        keyboard=keyboard,
                    )
                    return "edited"
                except DeliveryError as exc:
                    if exc.classification == "message_not_found":
                        await self._mark_missing(message)
                        return "missing"
                    if not exc.retryable or attempt == self._max_attempts - 1:
                        logger.warning(
                            "message_edit_failed",
                            extra={
                                "message_id": message.id,
                                "instance_id": message.instance_id,
                                "classification": exc.classification,
                                "error": str(exc),
                                "retryable": exc.retryable,
                            },
                        )
                        return "failed"
                    await self._sleeper.sleep(self._backoff(attempt, exc.retry_after))
            return "failed"

    async def _mark_missing(self, message: PollMessage) -> None:
        if message.id is None:
            return
        async with self._uow() as repos:
            await repos.messages.mark_missing(message.id, now=self._clock.now())
        logger.warning("poll_message_missing", extra={"message_id": message.id})

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        base: float = self._retry_base_seconds * (2**attempt)
        if retry_after is not None:
            base = max(base, min(retry_after, MAX_RETRY_AFTER_SECONDS))
        return base


#: The shape ``PollService`` depends on, kept explicit so tests can substitute a fake.
Broadcaster = Callable[[int], Awaitable[BroadcastResult]]
