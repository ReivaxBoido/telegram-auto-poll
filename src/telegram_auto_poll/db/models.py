"""SQLAlchemy 2.0 models for the CountMeIn schema (SPEC-003 §7).

Storage conventions:

* timestamps are ``INTEGER`` Unix epoch seconds in UTC (I-28);
* booleans are not stored in this model; lifecycle is an explicit status string;
* a naming convention is attached to the metadata so SQLite batch ``ALTER TABLE`` operations produce
  deterministic constraint names.

Repositories translate between these rows and the immutable domain entities; nothing here contains
business policy.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base carrying the project naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ChatRow(Base):
    """A registered group, supergroup, or channel."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("telegram_chat_id"),
        CheckConstraint("type IN ('group', 'supergroup', 'channel')", name="type_allowed"),
        CheckConstraint("is_active IN (0, 1)", name="is_active_bool"),
        CheckConstraint("length(title) BETWEEN 1 AND 255", name="title_length"),
    )


class PollRow(Base):
    """A sign-up poll definition."""

    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("length(title) BETWEEN 1 AND 300", name="title_length"),
        CheckConstraint("status IN ('draft', 'active', 'deleted')", name="status_allowed"),
        Index("ix_polls_status_created_at", "status", "created_at"),
    )


class PollOptionRow(Base):
    """One ordered option of a poll."""

    __tablename__ = "poll_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("polls.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    label_folded: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("poll_id", "position"),
        UniqueConstraint("poll_id", "label_folded"),
        CheckConstraint("position BETWEEN 0 AND 9", name="position_range"),
        CheckConstraint("length(label) BETWEEN 1 AND 100", name="label_length"),
    )


class PollInstanceRow(Base):
    """One publication/occurrence of a poll."""

    __tablename__ = "poll_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("polls.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    trigger: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    scheduled_for_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed', 'deleted')", name="status_allowed"),
        CheckConstraint("trigger IN ('manual', 'scheduled')", name="trigger_allowed"),
        Index("ix_poll_instances_poll_created", "poll_id", "created_at"),
        Index("ix_poll_instances_status", "status"),
    )


class PollMessageRow(Base):
    """One bot message for one instance and chat (or inline message)."""

    __tablename__ = "poll_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("poll_instances.id", ondelete="RESTRICT"),
        nullable=False,
    )
    chat_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("chats.id", ondelete="RESTRICT"),
        nullable=True,
    )
    inline_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'missing', 'skipped')",
            name="status_allowed",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint(
            "chat_id IS NOT NULL OR inline_message_id IS NOT NULL",
            name="target_present",
        ),
        Index("ix_poll_messages_pending", "status", "next_attempt_at_utc"),
        Index(
            "ux_poll_messages_instance_chat",
            "instance_id",
            "chat_id",
            unique=True,
            sqlite_where=text("chat_id IS NOT NULL"),
        ),
        Index(
            "ux_poll_messages_instance_inline",
            "instance_id",
            "inline_message_id",
            unique=True,
            sqlite_where=text("inline_message_id IS NOT NULL"),
        ),
    )


class PollResponseRow(Base):
    """A participant's membership in one option of one instance."""

    __tablename__ = "poll_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("poll_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    option_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("poll_options.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("instance_id", "option_id", "telegram_user_id"),
        CheckConstraint("length(first_name) BETWEEN 1 AND 64", name="first_name_length"),
        Index("ix_poll_responses_instance_option", "instance_id", "option_id"),
    )


class AuditEventRow(Base):
    """An immutable mutation record."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('poll', 'poll_instance', 'schedule', 'chat', 'delivery', 'config')",
            name="entity_type_allowed",
        ),
        CheckConstraint(
            "details_json IS NULL OR json_valid(details_json)",
            name="details_json_valid",
        ),
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_entity_type_entity_id", "entity_type", "entity_id"),
    )
