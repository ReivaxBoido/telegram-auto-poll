"""Immutable domain entities.

Entities are frozen dataclasses: nothing in the application mutates a loaded object, which keeps
services and tests free of accidental shared state. ``create`` classmethods are the only constructors
that validate user input; they are what the wizard and the services call.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time

from .enums import AuditAction, ChatType, DeliveryStatus, ScheduleKind, Trigger
from .errors import ValidationError
from .schedule_math import next_occurrence
from .validation import (
    validate_chat_type,
    validate_date_range,
    validate_day_of_month,
    validate_description,
    validate_once_instant,
    validate_options,
    validate_question,
    validate_timezone,
    validate_weekdays,
)


@dataclass(frozen=True, slots=True)
class Chat:
    """A registered group, supergroup, or channel."""

    telegram_chat_id: int
    type: ChatType
    title: str
    is_active: bool = True
    id: int | None = None
    created_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        telegram_chat_id: int,
        chat_type: str,
        title: str,
        now: datetime,
    ) -> Chat:
        resolved_type = validate_chat_type(chat_type)
        if telegram_chat_id >= 0:
            raise ValidationError(
                "telegram_chat_id",
                "a group, supergroup, or channel id is negative; paste the id from the chat",
            )
        clean_title = title.strip()
        if not clean_title:
            raise ValidationError("title", "the chat title must not be blank")
        return cls(
            telegram_chat_id=telegram_chat_id,
            type=resolved_type,
            title=clean_title,
            is_active=True,
            created_at=now,
        )


@dataclass(frozen=True, slots=True)
class Poll:
    """A reusable poll definition."""

    question: str
    options: tuple[str, ...]
    description: str | None = None
    is_anonymous: bool = True
    allows_multiple_answers: bool = False
    is_archived: bool = False
    targets: tuple[int, ...] = ()
    id: int | None = None
    created_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        question: str,
        options: tuple[str, ...] | list[str],
        now: datetime,
        description: str | None = None,
        is_anonymous: bool = True,
        allows_multiple_answers: bool = False,
        targets: tuple[int, ...] = (),
    ) -> Poll:
        return cls(
            question=validate_question(question),
            options=validate_options(options),
            description=validate_description(description),
            is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple_answers,
            targets=tuple(targets),
            created_at=now,
        )


@dataclass(frozen=True, slots=True)
class Schedule:
    """A recurrence rule and its next run, always stored in UTC."""

    poll_id: int
    kind: ScheduleKind
    timezone: str
    time_of_day: time | None = None
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None
    run_at: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_paused: bool = False
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    id: int | None = None
    created_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        poll_id: int,
        kind: ScheduleKind | str,
        timezone: str,
        now: datetime,
        time_of_day: time | None = None,
        weekdays: tuple[int, ...] | list[int] = (),
        day_of_month: int | None = None,
        run_at: datetime | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Schedule:
        try:
            resolved_kind = ScheduleKind(kind)
        except ValueError as exc:
            raise ValidationError(
                "kind",
                "schedule kind must be daily, weekly, monthly, or once",
            ) from exc

        resolved_tz = validate_timezone(timezone)
        resolved_start, resolved_end = validate_date_range(start_date, end_date)

        resolved_weekdays: tuple[int, ...] = ()
        resolved_day: int | None = None
        resolved_run_at: datetime | None = None
        resolved_time = time_of_day

        if resolved_kind is ScheduleKind.WEEKLY:
            resolved_weekdays = validate_weekdays(weekdays)
        elif resolved_kind is ScheduleKind.MONTHLY:
            if day_of_month is None:
                raise ValidationError("day_of_month", "a monthly schedule needs a day of the month")
            resolved_day = validate_day_of_month(day_of_month)
        elif resolved_kind is ScheduleKind.ONCE:
            if run_at is None:
                raise ValidationError("run_at", "a one-off schedule needs an instant")
            resolved_run_at = validate_once_instant(run_at, now=now)
            resolved_time = None
        elif resolved_time is None:
            raise ValidationError("time_of_day", "a daily schedule needs a time of day")

        schedule = cls(
            poll_id=poll_id,
            kind=resolved_kind,
            timezone=resolved_tz,
            time_of_day=resolved_time,
            weekdays=resolved_weekdays,
            day_of_month=resolved_day,
            run_at=resolved_run_at,
            start_date=resolved_start,
            end_date=resolved_end,
            created_at=now,
        )
        return replace(schedule, next_run_at=next_occurrence(schedule, after=now))


@dataclass(frozen=True, slots=True)
class Delivery:
    """One poll/chat/occurrence send attempt series."""

    poll_id: int
    chat_id: int
    trigger: Trigger
    status: DeliveryStatus = DeliveryStatus.PENDING
    scheduled_for: datetime | None = None
    schedule_id: int | None = None
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    telegram_message_id: int | None = None
    last_error: str | None = None
    skip_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    id: int | None = None
    created_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            DeliveryStatus.SENT,
            DeliveryStatus.FAILED,
            DeliveryStatus.SKIPPED,
        }

    def remaining_attempts(self, max_attempts: int) -> int:
        return max(max_attempts - self.attempt_count, 0)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable record of one mutation (FR-134)."""

    actor_user_id: int
    action: AuditAction
    entity_type: str
    entity_id: int
    details: dict[str, object] | None = None
    id: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PollRequest:
    """The exact payload handed to the ``PollSender`` port (FR-129)."""

    chat_id: int
    question: str
    options: tuple[str, ...]
    description: str | None = None
    is_anonymous: bool = True
    allows_multiple_answers: bool = False


@dataclass(frozen=True, slots=True)
class ChatInfo:
    """The subset of Telegram chat metadata the bot needs."""

    chat_id: int
    type: ChatType
    title: str
