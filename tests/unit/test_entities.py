"""Entity, enum, and error tests (SPEC-001 I-1 - I-6, §13.3)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time, timedelta

import pytest

from telegram_auto_poll.domain.entities import AuditEvent, Chat, Poll, Schedule
from telegram_auto_poll.domain.enums import (
    AuditAction,
    ChatType,
    DeliveryStatus,
    ScheduleKind,
    Trigger,
)
from telegram_auto_poll.domain.errors import (
    ConflictError,
    DeliveryError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from tests.factories import FIXED_NOW, make_chat, make_delivery, make_poll, make_schedule

# --------------------------------------------------------------------------------------------
# Enums: the stored strings are part of the database contract and must never drift.
# --------------------------------------------------------------------------------------------


def test_schedule_kind_values_are_stable() -> None:
    assert {kind.value for kind in ScheduleKind} == {"daily", "weekly", "monthly", "once"}


def test_delivery_status_values_are_stable() -> None:
    assert {status.value for status in DeliveryStatus} == {
        "pending",
        "sending",
        "sent",
        "failed",
        "skipped",
    }


def test_trigger_values_are_stable() -> None:
    assert {trigger.value for trigger in Trigger} == {"scheduled", "manual"}


def test_chat_type_values_are_stable() -> None:
    assert {chat_type.value for chat_type in ChatType} == {"group", "supergroup", "channel"}


def test_audit_action_values_are_dotted_verbs() -> None:
    assert AuditAction.POLL_CREATE.value == "poll.create"
    assert AuditAction.CHAT_REGISTER.value == "chat.register"
    assert AuditAction.SCHEDULE_PAUSE.value == "schedule.pause"


# --------------------------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------------------------


def test_chat_create_normalizes_the_chat_type() -> None:
    chat = Chat.create(telegram_chat_id=-100, chat_type="supergroup", title="Ops", now=FIXED_NOW)

    assert chat.type is ChatType.SUPERGROUP
    assert chat.is_active is True
    assert chat.created_at == FIXED_NOW


def test_chat_create_rejects_a_private_chat() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Chat.create(telegram_chat_id=42, chat_type="private", title="DM", now=FIXED_NOW)

    assert exc_info.value.field == "chat_type"


def test_chat_create_rejects_a_positive_chat_id() -> None:
    # Only groups, supergroups, and channels are registrable, and their ids are negative.
    with pytest.raises(ValidationError) as exc_info:
        Chat.create(telegram_chat_id=42, chat_type="group", title="Ops", now=FIXED_NOW)

    assert exc_info.value.field == "telegram_chat_id"


def test_chat_create_rejects_a_blank_title() -> None:
    with pytest.raises(ValidationError):
        Chat.create(telegram_chat_id=-100, chat_type="group", title="   ", now=FIXED_NOW)


# --------------------------------------------------------------------------------------------
# Poll
# --------------------------------------------------------------------------------------------


def test_poll_create_validates_and_normalizes() -> None:
    poll = Poll.create(
        question="  Ship it?  ",
        options=(" Yes ", "No"),
        description="  ",
        now=FIXED_NOW,
    )

    assert poll.question == "Ship it?"
    assert poll.options == ("Yes", "No")
    assert poll.description is None
    assert poll.is_anonymous is True
    assert poll.allows_multiple_answers is False
    assert poll.is_archived is False
    assert poll.created_at == FIXED_NOW


def test_poll_create_keeps_explicit_flags() -> None:
    poll = Poll.create(
        question="Pick many",
        options=("A", "B"),
        is_anonymous=False,
        allows_multiple_answers=True,
        now=FIXED_NOW,
    )

    assert poll.is_anonymous is False
    assert poll.allows_multiple_answers is True


def test_poll_create_rejects_one_option() -> None:
    with pytest.raises(ValidationError):
        Poll.create(question="Q", options=("only",), now=FIXED_NOW)


def test_poll_create_rejects_duplicate_options() -> None:
    with pytest.raises(ValidationError):
        Poll.create(question="Q", options=("A", "a"), now=FIXED_NOW)


# --------------------------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------------------------


def test_schedule_create_daily() -> None:
    schedule = Schedule.create(
        poll_id=7,
        kind=ScheduleKind.DAILY,
        timezone="Europe/London",
        time_of_day=time(9, 30),
        now=FIXED_NOW,
    )

    assert schedule.poll_id == 7
    assert schedule.kind is ScheduleKind.DAILY
    assert schedule.timezone == "Europe/London"
    assert schedule.time_of_day == time(9, 30)
    assert schedule.is_paused is False
    assert schedule.next_run_at == datetime(2026, 3, 2, 9, 30, tzinfo=UTC)


def test_schedule_create_weekly_requires_weekdays() -> None:
    with pytest.raises(ValidationError):
        Schedule.create(
            poll_id=7,
            kind=ScheduleKind.WEEKLY,
            timezone="UTC",
            time_of_day=time(9, 0),
            now=FIXED_NOW,
        )


def test_schedule_create_weekly_accepts_multiple_weekdays() -> None:
    schedule = Schedule.create(
        poll_id=7,
        kind=ScheduleKind.WEEKLY,
        timezone="UTC",
        time_of_day=time(9, 0),
        weekdays=(5, 1),
        now=FIXED_NOW,  # Sunday 2026-03-01
    )

    assert schedule.weekdays == (1, 5)
    assert schedule.next_run_at == datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def test_schedule_create_monthly_requires_a_day() -> None:
    with pytest.raises(ValidationError):
        Schedule.create(
            poll_id=7,
            kind=ScheduleKind.MONTHLY,
            timezone="UTC",
            time_of_day=time(9, 0),
            now=FIXED_NOW,
        )


def test_schedule_create_monthly_computes_the_next_run() -> None:
    schedule = Schedule.create(
        poll_id=7,
        kind=ScheduleKind.MONTHLY,
        timezone="UTC",
        time_of_day=time(9, 0),
        day_of_month=15,
        now=FIXED_NOW,
    )

    assert schedule.next_run_at == datetime(2026, 3, 15, 9, 0, tzinfo=UTC)


def test_schedule_create_once_rejects_a_past_instant() -> None:
    with pytest.raises(ValidationError):
        Schedule.create(
            poll_id=7,
            kind=ScheduleKind.ONCE,
            timezone="UTC",
            run_at=FIXED_NOW - timedelta(minutes=1),
            now=FIXED_NOW,
        )


def test_schedule_create_once_computes_the_utc_instant() -> None:
    run_at = FIXED_NOW + timedelta(hours=3)
    schedule = Schedule.create(
        poll_id=7,
        kind=ScheduleKind.ONCE,
        timezone="UTC",
        run_at=run_at,
        now=FIXED_NOW,
    )

    assert schedule.run_at == run_at
    assert schedule.next_run_at == run_at


def test_schedule_create_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        Schedule.create(
            poll_id=7,
            kind=ScheduleKind.DAILY,
            timezone="UTC",
            time_of_day=time(9, 0),
            start_date=date(2026, 4, 1),
            end_date=date(2026, 3, 1),
            now=FIXED_NOW,
        )


def test_schedule_create_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        Schedule.create(
            poll_id=7,
            kind="hourly",  # type: ignore[arg-type]
            timezone="UTC",
            time_of_day=time(9, 0),
            now=FIXED_NOW,
        )


# --------------------------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", [DeliveryStatus.SENT, DeliveryStatus.FAILED, DeliveryStatus.SKIPPED]
)
def test_terminal_delivery_statuses_are_terminal(status: DeliveryStatus) -> None:
    assert make_delivery(status=status).is_terminal is True


@pytest.mark.parametrize("status", [DeliveryStatus.PENDING, DeliveryStatus.SENDING])
def test_in_flight_delivery_statuses_are_not_terminal(status: DeliveryStatus) -> None:
    assert make_delivery(status=status).is_terminal is False


def test_delivery_progress_reports_attempts_left() -> None:
    delivery = make_delivery(attempt_count=1)

    assert delivery.remaining_attempts(max_attempts=3) == 2


def test_delivery_progress_never_goes_negative() -> None:
    delivery = make_delivery(attempt_count=5)

    assert delivery.remaining_attempts(max_attempts=3) == 0


# --------------------------------------------------------------------------------------------
# Immutability
# --------------------------------------------------------------------------------------------


def test_entities_are_frozen() -> None:
    poll = make_poll()

    with pytest.raises(FrozenInstanceError):
        poll.question = "changed"  # type: ignore[misc]


def test_chat_and_schedule_and_delivery_and_audit_are_frozen() -> None:
    for entity, attribute in (
        (make_chat(), "title"),
        (make_schedule(), "is_paused"),
        (make_delivery(), "status"),
        (
            AuditEvent(
                actor_user_id=1, action=AuditAction.POLL_CREATE, entity_type="poll", entity_id=1
            ),
            "action",
        ),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(entity, attribute, "changed")


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


def test_validation_error_is_a_domain_error_with_a_field() -> None:
    error = ValidationError(field="question", detail="question must be 1-300 characters (got 0)")

    assert isinstance(error, DomainError)
    assert error.field == "question"
    assert "300" in str(error)


def test_delivery_error_exposes_retryability() -> None:
    retryable = DeliveryError("timeout", retryable=True, retry_after=None)
    terminal = DeliveryError(
        "chat not found", retryable=False, retry_after=None, classification="bad_request"
    )

    assert retryable.retryable is True
    assert retryable.classification == "timeout"
    assert terminal.retryable is False
    assert terminal.classification == "bad_request"


def test_delivery_error_carries_retry_after() -> None:
    error = DeliveryError("rate limited", retryable=True, retry_after=7.0)

    assert error.retry_after == 7.0


@pytest.mark.parametrize("error_type", [PermissionDeniedError, NotFoundError, ConflictError])
def test_error_types_share_a_common_base(error_type: type[DomainError]) -> None:
    assert issubclass(error_type, DomainError)


def test_schedule_create_daily_requires_a_time_of_day() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Schedule.create(poll_id=7, kind=ScheduleKind.DAILY, timezone="UTC", now=FIXED_NOW)

    assert exc_info.value.field == "time_of_day"


def test_schedule_create_once_requires_an_instant() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Schedule.create(poll_id=7, kind=ScheduleKind.ONCE, timezone="UTC", now=FIXED_NOW)

    assert exc_info.value.field == "run_at"
