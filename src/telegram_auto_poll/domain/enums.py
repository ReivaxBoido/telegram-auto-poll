"""Enumerations stored verbatim in SQLite.

The string values are part of the database contract: changing one requires a migration and a spec
amendment, not just a code change.
"""

from __future__ import annotations

from enum import StrEnum


class PollStatus(StrEnum):
    """Lifecycle of a sign-up poll definition."""

    DRAFT = "draft"
    ACTIVE = "active"
    DELETED = "deleted"


class InstanceStatus(StrEnum):
    """Lifecycle of one publication/occurrence of a poll."""

    OPEN = "open"
    CLOSED = "closed"
    DELETED = "deleted"


class InstanceTrigger(StrEnum):
    """Why an instance exists."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"


class MessageStatus(StrEnum):
    """Delivery state of one bot message for one instance/chat."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    MISSING = "missing"
    SKIPPED = "skipped"


class SkipReason(StrEnum):
    """Why a message was skipped rather than sent."""

    CHAT_INACTIVE = "chat_inactive"
    INSTANCE_CLOSED = "instance_closed"
    INSTANCE_DELETED = "instance_deleted"
    POLL_DELETED = "poll_deleted"
    POLL_MISSING = "poll_missing"


class ChatType(StrEnum):
    """Telegram chat types the bot may post to."""

    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class VoteAction(StrEnum):
    """Outcome of a participant's option toggle."""

    ADDED = "added"
    REMOVED = "removed"


class AuditAction(StrEnum):
    """Dotted verbs recorded in ``audit_events.action``."""

    CHAT_REGISTER = "chat.register"
    CHAT_DEACTIVATE = "chat.deactivate"
    POLL_CREATE = "poll.create"
    POLL_PUBLISH = "poll.publish"
    POLL_CLOSE = "poll.close"
    POLL_REOPEN = "poll.reopen"
    POLL_DELETE = "poll.delete"
    POLL_REFRESH = "poll.refresh"
