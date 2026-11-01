"""Factories with valid defaults, so each test states only what it cares about."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from telegram_auto_poll.domain.entities import (
    AuditEvent,
    Chat,
    Poll,
    PollInstance,
    PollMessage,
    PollOption,
    PollResponse,
)
from telegram_auto_poll.domain.enums import (
    ChatType,
    InstanceStatus,
    InstanceTrigger,
    MessageStatus,
    PollStatus,
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


def make_option(position: int = 0, **overrides: Any) -> PollOption:
    defaults: dict[str, Any] = {
        "id": position + 1,
        "poll_id": 10,
        "position": position,
        "label": f"Option {position + 1}",
    }
    return PollOption(**{**defaults, **overrides})


def make_poll(**overrides: Any) -> Poll:
    defaults: dict[str, Any] = {
        "id": 10,
        "title": "Saturday football",
        "options": (make_option(0, label="In"), make_option(1, label="Out")),
        "status": PollStatus.DRAFT,
        "created_by": 100_000_001,
        "created_at": FIXED_NOW,
        "updated_at": FIXED_NOW,
    }
    return Poll(**{**defaults, **overrides})


def make_instance(**overrides: Any) -> PollInstance:
    defaults: dict[str, Any] = {
        "id": 100,
        "poll_id": 10,
        "status": InstanceStatus.OPEN,
        "trigger": InstanceTrigger.MANUAL,
        "scheduled_for": None,
        "created_by": 100_000_001,
        "created_at": FIXED_NOW,
        "closed_at": None,
    }
    return PollInstance(**{**defaults, **overrides})


def make_message(**overrides: Any) -> PollMessage:
    defaults: dict[str, Any] = {
        "id": 1000,
        "instance_id": 100,
        "chat_id": 1,
        "inline_message_id": None,
        "telegram_message_id": 500,
        "status": MessageStatus.SENT,
        "attempt_count": 0,
        "next_attempt_at": None,
        "last_error": None,
        "skip_reason": None,
        "created_at": FIXED_NOW,
        "sent_at": FIXED_NOW,
        "updated_at": FIXED_NOW,
    }
    return PollMessage(**{**defaults, **overrides})


def make_response(**overrides: Any) -> PollResponse:
    defaults: dict[str, Any] = {
        "id": 1,
        "instance_id": 100,
        "option_id": 1,
        "telegram_user_id": 100_000_001,
        "first_name": "Alice",
        "created_at": FIXED_NOW,
        "updated_at": FIXED_NOW,
    }
    return PollResponse(**{**defaults, **overrides})


def make_audit_event(**overrides: Any) -> AuditEvent:
    defaults: dict[str, Any] = {
        "id": 1,
        "actor_user_id": 100_000_001,
        "action": "poll.create",
        "entity_type": "poll",
        "entity_id": 10,
        "details": {"title": "Saturday football"},
        "created_at": FIXED_NOW,
    }
    return AuditEvent(**{**defaults, **overrides})
