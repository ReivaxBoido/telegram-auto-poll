"""SQLAlchemy 2.0 models for the six tables in constitution §5.2.

Storage conventions:

* timestamps are ``INTEGER`` Unix epoch seconds in UTC (I-14);
* booleans are ``INTEGER`` guarded by ``CHECK (col IN (0,1))``;
* JSON payloads are ``TEXT`` guarded by ``json_valid``;
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
    """A reusable poll definition."""

    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    options_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_anonymous: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    allows_multiple_answers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("length(question) BETWEEN 1 AND 300", name="question_length"),
        CheckConstraint(
            "description IS NULL OR length(description) BETWEEN 0 AND 1024",
            name="description_length",
        ),
        CheckConstraint("json_valid(options_json)", name="options_json_valid"),
        CheckConstraint(
            "json_array_length(options_json) BETWEEN 2 AND 12",
            name="options_count",
        ),
        CheckConstraint("is_anonymous IN (0, 1)", name="is_anonymous_bool"),
        CheckConstraint("allows_multiple_answers IN (0, 1)", name="multiple_answers_bool"),
        CheckConstraint("is_active IN (0, 1)", name="is_active_bool"),
    )


class PollTargetRow(Base):
    """Poll ↔ chat association."""

    __tablename__ = "poll_targets"

    poll_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("polls.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    chat_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chats.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class ScheduleRow(Base):
    """A recurrence rule and its next run."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("polls.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    time_of_day: Mapped[str | None] = mapped_column(Text, nullable=True)
    weekdays_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_at_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    starts_on_local_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    ends_on_local_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_paused: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_run_at_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_run_at_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('daily', 'weekly', 'monthly', 'once')", name="kind_allowed"),
        CheckConstraint("is_paused IN (0, 1)", name="is_paused_bool"),
        CheckConstraint(
            "day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31",
            name="day_of_month_range",
        ),
        CheckConstraint("length(timezone) > 0", name="timezone_present"),
        Index("ix_schedules_due", "is_paused", "next_run_at_utc"),
    )


class DeliveryRow(Base):
    """One poll/chat/occurrence send attempt series."""

    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("polls.id", ondelete="RESTRICT"),
        nullable=False,
    )
    chat_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chats.id", ondelete="RESTRICT"),
        nullable=False,
    )
    schedule_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("schedules.id", ondelete="SET NULL"),
        nullable=True,
    )
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "trigger IN ('scheduled', 'manual')",
            name="trigger_allowed",
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'skipped')",
            name="status_allowed",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        Index("ix_deliveries_pending", "status", "next_attempt_at_utc"),
        Index(
            "ux_delivery_occurrence",
            "schedule_id",
            "scheduled_for_utc",
            "chat_id",
            unique=True,
            sqlite_where=text("schedule_id IS NOT NULL"),
        ),
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
            "entity_type IN ('poll', 'schedule', 'chat', 'delivery', 'config')",
            name="entity_type_allowed",
        ),
        CheckConstraint(
            "details_json IS NULL OR json_valid(details_json)",
            name="details_json_valid",
        ),
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_entity_type_entity_id", "entity_type", "entity_id"),
    )
