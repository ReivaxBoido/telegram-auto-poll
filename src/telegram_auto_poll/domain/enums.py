"""Enumerations stored verbatim in SQLite.

The string values are part of the database contract: changing one requires a migration and a spec
amendment, not just a code change.
"""

from __future__ import annotations

from enum import StrEnum


class ScheduleKind(StrEnum):
    """Recurrence rule of a schedule."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ONCE = "once"


class DeliveryStatus(StrEnum):
    """Lifecycle of a single poll/chat/occurrence delivery."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class Trigger(StrEnum):
    """Why a delivery row exists."""

    SCHEDULED = "scheduled"
    MANUAL = "manual"


class ChatType(StrEnum):
    """Telegram chat types the bot may post to."""

    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class SkipReason(StrEnum):
    """Why a delivery was skipped rather than sent."""

    MISSED_GRACE_WINDOW = "missed_grace_window"
    CHAT_INACTIVE = "chat_inactive"
    POLL_ARCHIVED = "poll_archived"
    POLL_MISSING = "poll_missing"


class AuditAction(StrEnum):
    """Dotted verbs recorded in ``audit_events.action``."""

    CHAT_REGISTER = "chat.register"
    CHAT_DEACTIVATE = "chat.deactivate"
    POLL_CREATE = "poll.create"
    SCHEDULE_CREATE = "schedule.create"
    SCHEDULE_PAUSE = "schedule.pause"
    SCHEDULE_RESUME = "schedule.resume"
    SCHEDULE_DELETE = "schedule.delete"
    DELIVERY_SEND = "delivery.send"
