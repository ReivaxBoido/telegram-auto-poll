"""CountMeIn core schema (SPEC-003 §7).

This is a destructive reset. It aborts when the native-poll tables contain rows and
``ALLOW_DESTRUCTIVE_MIGRATION`` is not ``true``, so an unexpected production database is never wiped
silently. The startup path takes a pre-migration backup before this runs.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TABLES = ("polls", "poll_targets", "schedules", "deliveries")


def _destructive_allowed() -> bool:
    return os.environ.get("ALLOW_DESTRUCTIVE_MIGRATION", "false").strip().lower() == "true"


def _assert_safe_to_reset() -> None:
    if _destructive_allowed():
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    counts: dict[str, int] = {}
    for table in _OLD_TABLES:
        if table not in tables:
            continue
        count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        if count:
            counts[table] = int(count)
    if counts:
        detail = ", ".join(f"{table}={count}" for table, count in counts.items())
        raise RuntimeError(
            "refusing destructive CountMeIn migration: old tables contain rows "
            f"({detail}); take a backup and set ALLOW_DESTRUCTIVE_MIGRATION=true for this run"
        )


def upgrade() -> None:
    _assert_safe_to_reset()

    op.drop_table("deliveries")
    op.drop_table("poll_targets")
    op.drop_table("schedules")
    op.drop_table("polls")

    op.create_table(
        "polls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(title) BETWEEN 1 AND 300",
            name=op.f("ck_polls_title_length"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'deleted')",
            name=op.f("ck_polls_status_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_polls")),
    )
    op.create_index(
        "ix_polls_status_created_at",
        "polls",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "poll_options",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("poll_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("label_folded", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 9",
            name=op.f("ck_poll_options_position_range"),
        ),
        sa.CheckConstraint(
            "length(label) BETWEEN 1 AND 100",
            name=op.f("ck_poll_options_label_length"),
        ),
        sa.ForeignKeyConstraint(
            ["poll_id"],
            ["polls.id"],
            name=op.f("fk_poll_options_poll_id_polls"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poll_options")),
        sa.UniqueConstraint(
            "poll_id",
            "position",
            name=op.f("uq_poll_options_poll_id_position"),
        ),
        sa.UniqueConstraint(
            "poll_id",
            "label_folded",
            name=op.f("uq_poll_options_poll_id_label_folded"),
        ),
    )

    op.create_table(
        "poll_instances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("poll_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("scheduled_for_utc", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("closed_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'deleted')",
            name=op.f("ck_poll_instances_status_allowed"),
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled')",
            name=op.f("ck_poll_instances_trigger_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["poll_id"],
            ["polls.id"],
            name=op.f("fk_poll_instances_poll_id_polls"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poll_instances")),
    )
    op.create_index(
        "ix_poll_instances_poll_created",
        "poll_instances",
        ["poll_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_poll_instances_status",
        "poll_instances",
        ["status"],
        unique=False,
    )

    op.create_table(
        "poll_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=True),
        sa.Column("inline_message_id", sa.Text(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at_utc", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'missing', 'skipped')",
            name=op.f("ck_poll_messages_status_allowed"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_poll_messages_attempt_count_non_negative"),
        ),
        sa.CheckConstraint(
            "chat_id IS NOT NULL OR inline_message_id IS NOT NULL",
            name=op.f("ck_poll_messages_target_present"),
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            name=op.f("fk_poll_messages_chat_id_chats"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["poll_instances.id"],
            name=op.f("fk_poll_messages_instance_id_poll_instances"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poll_messages")),
    )
    op.create_index(
        "ix_poll_messages_pending",
        "poll_messages",
        ["status", "next_attempt_at_utc"],
        unique=False,
    )
    op.create_index(
        "ux_poll_messages_instance_chat",
        "poll_messages",
        ["instance_id", "chat_id"],
        unique=True,
        sqlite_where=sa.text("chat_id IS NOT NULL"),
    )
    op.create_index(
        "ux_poll_messages_instance_inline",
        "poll_messages",
        ["instance_id", "inline_message_id"],
        unique=True,
        sqlite_where=sa.text("inline_message_id IS NOT NULL"),
    )

    op.create_table(
        "poll_responses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("option_id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(first_name) BETWEEN 1 AND 64",
            name=op.f("ck_poll_responses_first_name_length"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["poll_instances.id"],
            name=op.f("fk_poll_responses_instance_id_poll_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["option_id"],
            ["poll_options.id"],
            name=op.f("fk_poll_responses_option_id_poll_options"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poll_responses")),
        sa.UniqueConstraint(
            "instance_id",
            "option_id",
            "telegram_user_id",
            name=op.f("uq_poll_responses_instance_id_option_id_telegram_user_id"),
        ),
    )
    op.create_index(
        "ix_poll_responses_instance_option",
        "poll_responses",
        ["instance_id", "option_id"],
        unique=False,
    )

    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_constraint(
            "entity_type_allowed",
            type_="check",
        )
        batch_op.create_check_constraint(
            "entity_type_allowed",
            "entity_type IN ('poll', 'poll_instance', 'schedule', 'chat', 'delivery', 'config')",
        )


def downgrade() -> None:
    """Forward-only migration; rollback is restore-from-backup (SPEC-003 §13)."""
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_constraint(
            "entity_type_allowed",
            type_="check",
        )
        batch_op.create_check_constraint(
            "entity_type_allowed",
            "entity_type IN ('poll', 'schedule', 'chat', 'delivery', 'config')",
        )

    op.drop_index("ix_poll_responses_instance_option", table_name="poll_responses")
    op.drop_table("poll_responses")
    op.drop_index("ux_poll_messages_instance_inline", table_name="poll_messages")
    op.drop_index("ux_poll_messages_instance_chat", table_name="poll_messages")
    op.drop_index("ix_poll_messages_pending", table_name="poll_messages")
    op.drop_table("poll_messages")
    op.drop_index("ix_poll_instances_status", table_name="poll_instances")
    op.drop_index("ix_poll_instances_poll_created", table_name="poll_instances")
    op.drop_table("poll_instances")
    op.drop_table("poll_options")
    op.drop_index("ix_polls_status_created_at", table_name="polls")
    op.drop_table("polls")
