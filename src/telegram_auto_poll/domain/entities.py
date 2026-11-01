"""Immutable domain entities.

Entities are frozen dataclasses: nothing in the application mutates a loaded object, which keeps
services and tests free of accidental shared state. ``create`` classmethods are the only constructors
that validate user input; they are what the wizard and the services call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import ChatType, InstanceStatus, InstanceTrigger, MessageStatus, PollStatus
from .errors import ValidationError
from .validation import (
    validate_chat_type,
    validate_option_label,
    validate_poll_options,
    validate_poll_title,
)

#: A Telegram-ready button row: ``(label, callback_data)``. The bot adapter converts it to PTB.
type Button = tuple[str, str]
type Keyboard = tuple[tuple[Button, ...], ...]


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
class PollOption:
    """One ordered answer option of a poll."""

    label: str
    position: int
    poll_id: int | None = None
    id: int | None = None

    @classmethod
    def create(cls, label: str, *, position: int, poll_id: int | None = None) -> PollOption:
        """Validate and build an option at ``position``."""
        return cls(
            label=validate_option_label(label),
            position=position,
            poll_id=poll_id,
        )


@dataclass(frozen=True, slots=True)
class Poll:
    """A reusable sign-up poll definition: a title and 1-10 ordered options."""

    title: str
    options: tuple[PollOption, ...]
    status: PollStatus = PollStatus.DRAFT
    created_by: int = 0
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        title: str,
        options: tuple[str, ...] | list[str],
        created_by: int,
        now: datetime,
    ) -> Poll:
        """Validate and build a draft poll. Options are not persisted until the service runs."""
        labels = validate_poll_options(options)
        return cls(
            title=validate_poll_title(title),
            options=tuple(
                PollOption.create(label, position=position) for position, label in enumerate(labels)
            ),
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    def option(self, option_id: int) -> PollOption | None:
        """Return the option with ``option_id``, or ``None``."""
        return next((option for option in self.options if option.id == option_id), None)


@dataclass(frozen=True, slots=True)
class PollInstance:
    """One publication/occurrence of a poll.

    Responses belong to the instance, so a later occurrence starts with a fresh list.
    """

    poll_id: int
    status: InstanceStatus = InstanceStatus.OPEN
    trigger: InstanceTrigger = InstanceTrigger.MANUAL
    scheduled_for: datetime | None = None
    created_by: int = 0
    id: int | None = None
    created_at: datetime | None = None
    closed_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        """Whether the instance currently accepts votes."""
        return self.status is InstanceStatus.OPEN


@dataclass(frozen=True, slots=True)
class PollMessage:
    """One bot message for one instance and chat (or inline message)."""

    instance_id: int
    chat_id: int | None = None
    inline_message_id: str | None = None
    telegram_message_id: int | None = None
    status: MessageStatus = MessageStatus.PENDING
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    skip_reason: str | None = None
    id: int | None = None
    created_at: datetime | None = None
    sent_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether no automatic retry will happen."""
        return self.status in {
            MessageStatus.SENT,
            MessageStatus.FAILED,
            MessageStatus.MISSING,
            MessageStatus.SKIPPED,
        }


@dataclass(frozen=True, slots=True)
class PollResponse:
    """A participant's membership in one option of one instance."""

    instance_id: int
    option_id: int
    telegram_user_id: int
    first_name: str
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OptionResponses:
    """One option plus its respondents, ordered by response time."""

    option: PollOption
    respondents: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PollButton:
    """A vote button with its already-encoded callback data."""

    label: str
    callback_data: str


@dataclass(frozen=True, slots=True)
class PollRender:
    """The rendered poll message and the buttons it should carry."""

    text: str
    buttons: tuple[PollButton, ...] = ()
    closed: bool = False


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable record of one admin mutation (FR-336)."""

    actor_user_id: int
    action: str
    entity_type: str
    entity_id: int
    details: dict[str, object] | None = None
    id: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BroadcastResult:
    """What one render/redact pass changed."""

    edited: int = 0
    missing: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class ChatInfo:
    """The subset of Telegram chat metadata the bot needs."""

    chat_id: int
    type: ChatType
    title: str
