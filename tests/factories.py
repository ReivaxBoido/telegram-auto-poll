"""Factories with valid defaults, so each test states only what it cares about."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from telegram_auto_poll.domain.entities import AuditEvent, Chat, Delivery, Poll, Schedule
from telegram_auto_poll.domain.enums import (
    AuditAction,
    ChatType,
    DeliveryStatus,
    ScheduleKind,
    Trigger,
)

FIXED_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def make_chat(**overrides: Any) -> Chat:
    defaults: dict[str, Any] = {
        "id": 1,
        "telegram_chat_id": -1_001_234_567_890,
        "type": ChatType.SUPERGROUP,
        "title": "Automation test group",
        "is_active": True,
        "created_at": FIXED_NOW,
    }
    return Chat(**{**defaults, **overrides})


def make_poll(**overrides: Any) -> Poll:
    defaults: dict[str, Any] = {
        "id": 10,
        "question": "What should we ship next?",
        "description": "Pick one option.",
        "options": ("Reliability", "Speed", "Docs"),
        "is_anonymous": True,
        "allows_multiple_answers": False,
        "is_archived": False,
        "targets": (1,),
        "created_at": FIXED_NOW,
    }
    return Poll(**{**defaults, **overrides})


def make_schedule(**overrides: Any) -> Schedule:
    defaults: dict[str, Any] = {
        "id": 100,
        "poll_id": 10,
        "kind": ScheduleKind.DAILY,
        "timezone": "UTC",
        "time_of_day": time(9, 0),
        "weekdays": (),
        "day_of_month": None,
        "run_at": None,
        "start_date": None,
        "end_date": None,
        "is_paused": False,
        "next_run_at": datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        "last_run_at": None,
        "created_at": FIXED_NOW,
    }
    return Schedule(**{**defaults, **overrides})


def make_delivery(**overrides: Any) -> Delivery:
    defaults: dict[str, Any] = {
        "id": 1000,
        "poll_id": 10,
        "chat_id": 1,
        "schedule_id": 100,
        "scheduled_for": datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        "trigger": Trigger.SCHEDULED,
        "status": DeliveryStatus.PENDING,
        "attempt_count": 0,
        "next_attempt_at": datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        "telegram_message_id": None,
        "last_error": None,
        "skip_reason": None,
        "started_at": None,
        "completed_at": None,
        "created_at": FIXED_NOW,
    }
    return Delivery(**{**defaults, **overrides})


def make_audit_event(**overrides: Any) -> AuditEvent:
    defaults: dict[str, Any] = {
        "id": 1,
        "actor_user_id": 100_000_001,
        "action": AuditAction.POLL_CREATE,
        "entity_type": "poll",
        "entity_id": 10,
        "details": {"question": "What should we ship next?"},
        "created_at": FIXED_NOW,
    }
    return AuditEvent(**{**defaults, **overrides})


def make_local_date(year: int, month: int, day: int) -> date:
    return date(year, month, day)
