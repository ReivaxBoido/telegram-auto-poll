"""Delivery planning, sending, retries, and skips (FR-125 - FR-133).

Two invariants shape this module:

* **Nothing is sent inside a transaction.** The row is claimed in one unit of work, Telegram is
  called with no session open, and the outcome is written in another unit of work. SQLite is never
  blocked on the network.
* **At most once per occurrence and chat.** Creation goes through the partial unique index; a crash
  after the claim leaves a ``sending`` row that startup marks ``failed`` instead of resending.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from ..db.uow import Repositories, UnitOfWork
from ..domain.entities import AuditEvent, Chat, Delivery, Poll, PollRequest
from ..domain.enums import AuditAction, DeliveryStatus, SkipReason, Trigger
from ..domain.errors import ConflictError, DeliveryError, NotFoundError, ValidationError
from ..domain.ports import AdminNotifier, Clock, Pacer, PollSender
from ..domain.schedule_math import next_occurrence

#: Telegram allows 30 messages per second overall; retries older than five minutes are pointless.
MAX_RETRY_DELAY_SECONDS = 300.0
MAX_ERROR_LENGTH = 500
MIN_JITTER = 0.8
JITTER_RANGE = 0.4

#: A BotFather token, should it ever appear in an exception message.
_TOKEN_PATTERN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")


def sanitize_error(message: str) -> str:
    """Remove token-shaped secrets and bound the length before persisting an error."""
    cleaned = _TOKEN_PATTERN.sub("[redacted]", message).strip()
    return cleaned[:MAX_ERROR_LENGTH] or "unknown error"


@dataclass(frozen=True, slots=True)
class TickOutcome:
    """What one planning pass created."""

    schedules: int = 0
    created: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class SendOutcome:
    """The terminal-or-retry state of one claimed delivery."""

    delivery_id: int
    status: DeliveryStatus
    attempts: int


class DeliveryService:
    """Plans occurrences, dispatches claimed rows, and handles manual sends."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        sender: PollSender,
        notifier: AdminNotifier,
        pacer: Pacer,
        clock: Clock,
        grace_seconds: int = 120,
        max_attempts: int = 3,
        retry_base_seconds: float = 2.0,
        batch_size: int = 50,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self._uow = uow
        self._sender = sender
        self._notifier = notifier
        self._pacer = pacer
        self._clock = clock
        self._grace_seconds = grace_seconds
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._batch_size = batch_size
        self._random = random_source

    # ----------------------------------------------------------------------------------------
    # Planning
    # ----------------------------------------------------------------------------------------

    async def plan_due(self) -> TickOutcome:
        """Create delivery rows for every due occurrence, one transaction per schedule."""
        now = self._clock.now()
        async with self._uow() as repos:
            due = await repos.schedules.due_ids_before(now, limit=self._batch_size)

        totals = TickOutcome()
        for schedule_id in due:
            outcome = await self._plan_one(schedule_id, now)
            totals = TickOutcome(
                schedules=totals.schedules + outcome.schedules,
                created=totals.created + outcome.created,
                skipped=totals.skipped + outcome.skipped,
            )
        return totals

    async def _plan_one(self, schedule_id: int, now: datetime) -> TickOutcome:
        async with self._uow() as repos:
            schedule = await repos.schedules.get(schedule_id)
            if schedule is None or schedule.is_paused or schedule.next_run_at is None:
                return TickOutcome()
            poll = await repos.polls.get(schedule.poll_id)
            if poll is None:
                return TickOutcome()

            occurrence = schedule.next_run_at
            active = await repos.chats.active_ids()
            rows = self._rows_for_occurrence(schedule_id, poll, occurrence, active, now)
            inserted = await repos.deliveries.insert_many_ignore(rows, now=now)
            await repos.schedules.update_next_run(
                schedule_id,
                next_run_at=next_occurrence(schedule, after=occurrence),
                last_run_at=occurrence,
                now=now,
            )

        pending = sum(1 for row in rows if row.status is DeliveryStatus.PENDING)
        return TickOutcome(
            schedules=1,
            created=min(inserted, pending),
            skipped=len(rows) - pending,
        )

    def _rows_for_occurrence(
        self,
        schedule_id: int,
        poll: Poll,
        occurrence: datetime,
        active_chats: set[int],
        now: datetime,
    ) -> list[Delivery]:
        late = (now - occurrence).total_seconds() > self._grace_seconds
        rows: list[Delivery] = []
        for chat_id in poll.targets:
            reason = self._skip_reason(poll, chat_id, active_chats, late)
            rows.append(
                Delivery(
                    poll_id=cast("int", poll.id),
                    chat_id=chat_id,
                    schedule_id=schedule_id,
                    trigger=Trigger.SCHEDULED,
                    status=DeliveryStatus.SKIPPED if reason else DeliveryStatus.PENDING,
                    scheduled_for=occurrence,
                    next_attempt_at=None if reason else occurrence,
                    skip_reason=reason.value if reason else None,
                    completed_at=now if reason else None,
                    created_at=now,
                )
            )
        return rows

    def _skip_reason(
        self,
        poll: Poll,
        chat_id: int,
        active_chats: set[int],
        late: bool,
    ) -> SkipReason | None:
        if poll.is_archived:
            return SkipReason.POLL_ARCHIVED
        if late:
            return SkipReason.MISSED_GRACE_WINDOW
        if chat_id not in active_chats:
            return SkipReason.CHAT_INACTIVE
        return None

    # ----------------------------------------------------------------------------------------
    # Dispatch
    # ----------------------------------------------------------------------------------------

    async def dispatch(self, *, limit: int | None = None) -> list[SendOutcome]:
        """Claim up to ``limit`` due rows and send each one, outside any transaction."""
        now = self._clock.now()
        async with self._uow() as repos:
            claimed = await repos.deliveries.claim_due(now, limit=limit or self._batch_size)

        outcomes: list[SendOutcome] = []
        for delivery in claimed:
            outcomes.append(await self._send_one(delivery, now))
        return outcomes

    async def _send_one(self, delivery: Delivery, now: datetime) -> SendOutcome:
        delivery_id = cast("int", delivery.id)
        context = await self._load_context(delivery)

        if isinstance(context, SkipReason):
            return await self._skip(delivery_id, context, delivery.attempt_count, now)

        poll, chat = context
        if chat is None or not chat.is_active:
            return await self._skip(
                delivery_id, SkipReason.CHAT_INACTIVE, delivery.attempt_count, now
            )
        if poll.is_archived:
            return await self._skip(
                delivery_id, SkipReason.POLL_ARCHIVED, delivery.attempt_count, now
            )

        # Deliveries reference the surrogate chats.id; Telegram needs the real chat id.
        await self._pacer.acquire(chat.telegram_chat_id)
        request = PollRequest(
            chat_id=chat.telegram_chat_id,
            question=poll.question,
            options=poll.options,
            description=poll.description,
            is_anonymous=poll.is_anonymous,
            allows_multiple_answers=poll.allows_multiple_answers,
        )
        try:
            message_id = await self._sender.send_poll(request)
        except DeliveryError as error:
            return await self._handle_error(delivery_id, delivery, error, now)

        async with self._uow() as repos:
            await repos.deliveries.mark_sent(delivery_id, telegram_message_id=message_id, now=now)
            await self._audit(
                repos, delivery, AuditAction.DELIVERY_SEND, "sent", delivery.attempt_count, now
            )
        return SendOutcome(
            delivery_id=delivery_id,
            status=DeliveryStatus.SENT,
            attempts=delivery.attempt_count,
        )

    async def _load_context(self, delivery: Delivery) -> tuple[Poll, Chat | None] | SkipReason:
        async with self._uow() as repos:
            poll = await repos.polls.get(delivery.poll_id)
            if poll is None:
                return SkipReason.POLL_MISSING
            chat = await repos.chats.get(delivery.chat_id)
        return poll, chat

    async def _handle_error(
        self,
        delivery_id: int,
        delivery: Delivery,
        error: DeliveryError,
        now: datetime,
    ) -> SendOutcome:
        message = sanitize_error(str(error))
        if error.retryable and delivery.attempt_count < self._max_attempts:
            next_attempt = self._next_attempt(delivery.attempt_count, error, now)
            async with self._uow() as repos:
                await repos.deliveries.mark_retry(
                    delivery_id, next_attempt_at=next_attempt, error=message, now=now
                )
                await self._audit(
                    repos,
                    delivery,
                    AuditAction.DELIVERY_SEND,
                    "retry",
                    delivery.attempt_count,
                    now,
                )
            return SendOutcome(
                delivery_id=delivery_id,
                status=DeliveryStatus.PENDING,
                attempts=delivery.attempt_count,
            )

        async with self._uow() as repos:
            await repos.deliveries.mark_failed(delivery_id, error=message, now=now)
            await self._audit(
                repos,
                delivery,
                AuditAction.DELIVERY_SEND,
                "failed",
                delivery.attempt_count,
                now,
            )

        await self._notifier.notify_admins(
            f"delivery {delivery_id} failed permanently after {delivery.attempt_count} "
            f"attempt(s): {message}"
        )
        return SendOutcome(
            delivery_id=delivery_id,
            status=DeliveryStatus.FAILED,
            attempts=delivery.attempt_count,
        )

    def _next_attempt(self, attempt: int, error: DeliveryError, now: datetime) -> datetime:
        delay = self._retry_base_seconds * (2 ** max(attempt - 1, 0))
        delay *= MIN_JITTER + JITTER_RANGE * self._random()
        if error.retry_after is not None:
            delay = max(delay, error.retry_after)
        return now + timedelta(seconds=min(delay, MAX_RETRY_DELAY_SECONDS))

    async def _skip(
        self, delivery_id: int, reason: SkipReason, attempts: int, now: datetime
    ) -> SendOutcome:
        async with self._uow() as repos:
            await repos.deliveries.mark_skipped(delivery_id, reason=reason.value, now=now)
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=0,
                    action=AuditAction.DELIVERY_SEND,
                    entity_type="delivery",
                    entity_id=delivery_id,
                    details={"status": "skipped", "reason": reason.value},
                    created_at=now,
                ),
                now=now,
            )
        return SendOutcome(
            delivery_id=delivery_id, status=DeliveryStatus.SKIPPED, attempts=attempts
        )

    async def _audit(
        self,
        repos: Repositories,
        delivery: Delivery,
        action: AuditAction,
        status: str,
        attempts: int,
        now: datetime,
    ) -> None:
        await repos.audit.add(
            AuditEvent(
                actor_user_id=0,
                action=action,
                entity_type="delivery",
                entity_id=cast("int", delivery.id),
                details={
                    "status": status,
                    "poll_id": delivery.poll_id,
                    "chat_id": delivery.chat_id,
                    "attempt": attempts,
                },
                created_at=now,
            ),
            now=now,
        )

    # ----------------------------------------------------------------------------------------
    # Manual sends and crash recovery
    # ----------------------------------------------------------------------------------------

    async def send_now(self, poll_id: int, *, actor_user_id: int) -> int:
        """Queue a manual delivery to every active target (FR-132).

        Raises:
            NotFoundError: the poll does not exist.
            ValidationError: the poll is archived.
            ConflictError: the poll has no active target.
        """
        now = self._clock.now()
        async with self._uow() as repos:
            poll = await repos.polls.get(poll_id)
            if poll is None:
                raise NotFoundError(f"poll {poll_id} does not exist")
            if poll.is_archived:
                raise ValidationError("poll", "an archived poll cannot be sent")
            active = await repos.chats.active_ids()
            targets = [chat_id for chat_id in poll.targets if chat_id in active]
            if not targets:
                raise ConflictError("this poll has no active target chat")

            rows = [
                Delivery(
                    poll_id=poll_id,
                    chat_id=chat_id,
                    trigger=Trigger.MANUAL,
                    status=DeliveryStatus.PENDING,
                    scheduled_for=now,
                    next_attempt_at=None,
                    created_at=now,
                )
                for chat_id in targets
            ]
            inserted = await repos.deliveries.insert_many_ignore(rows, now=now)
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.DELIVERY_SEND,
                    entity_type="poll",
                    entity_id=poll_id,
                    details={"trigger": Trigger.MANUAL.value, "targets": targets},
                    created_at=now,
                ),
                now=now,
            )
        return inserted

    async def recover(self) -> int:
        """Fail rows left in ``sending`` by a crash; they are never retried (at-most-once)."""
        now = self._clock.now()
        async with self._uow() as repos:
            return await repos.deliveries.fail_stale_sending(
                reason="interrupted before the result was recorded", now=now
            )
