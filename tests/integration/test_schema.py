"""Schema and constraint tests: the database enforces the invariants, not just the services.

Every assertion here is a negative test: the insert must be rejected, or the delivery must not be
duplicated. That is deliberate — these constraints are the last line of defence for I-2, I-9, I-10,
and I-11.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.migrate import current_revision
from telegram_auto_poll.db.models import ChatRow
from telegram_auto_poll.db.repositories.chats import ChatRepository
from telegram_auto_poll.db.repositories.deliveries import DeliveryRepository
from telegram_auto_poll.domain.entities import Delivery
from telegram_auto_poll.domain.enums import DeliveryStatus, Trigger
from tests.conftest import Seed
from tests.factories import FIXED_NOW, make_chat, make_delivery


async def _reject(
    factory: async_sessionmaker[AsyncSession],
    statement: str,
    params: Mapping[str, Any] | None = None,
    *,
    error: type[Exception] = IntegrityError,
) -> None:
    # SQLite reports a malformed JSON argument as ``OperationalError`` rather than a constraint
    # failure, because ``json_array_length`` raises while evaluating the CHECK.
    with pytest.raises(error):
        async with session_scope(factory) as session:
            await session.execute(text(statement), params or {})
            await session.flush()


async def test_the_database_is_at_the_expected_revision(engine: AsyncEngine) -> None:
    assert await current_revision(engine) == "0001"


async def test_connection_pragmas_are_applied(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        journal = await connection.scalar(text("PRAGMA journal_mode"))
        foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
        busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))

    assert str(journal).lower() == "wal"
    assert foreign_keys == 1
    assert busy_timeout == 1000


async def test_foreign_keys_are_on_for_every_new_connection(engine: AsyncEngine) -> None:
    # A second connection must inherit the listener, not just the first one.
    async with engine.connect() as first, engine.connect() as second:
        assert await first.scalar(text("PRAGMA foreign_keys")) == 1
        assert await second.scalar(text("PRAGMA foreign_keys")) == 1


async def test_telegram_chat_id_is_unique(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        await ChatRepository(session).upsert(make_chat(), now=FIXED_NOW)

    with pytest.raises(IntegrityError):
        async with session_scope(factory) as session:
            # Bypass the repository's upsert to reach the raw unique constraint.
            session.add(
                ChatRow(
                    telegram_chat_id=make_chat().telegram_chat_id,
                    type="supergroup",
                    title="Duplicate",
                    is_active=1,
                    created_at=1,
                    updated_at=1,
                )
            )
            await session.flush()


async def test_question_length_is_enforced(factory: async_sessionmaker[AsyncSession]) -> None:
    await _reject(
        factory,
        "INSERT INTO polls (question, options_json, is_anonymous, allows_multiple_answers,"
        " is_active, created_at, updated_at) VALUES (:question, :options, 1, 0, 1, 1, 1)",
        {"question": "q" * 301, "options": '["a","b"]'},
    )


async def test_options_must_be_valid_json(factory: async_sessionmaker[AsyncSession]) -> None:
    await _reject(
        factory,
        "INSERT INTO polls (question, options_json, is_anonymous, allows_multiple_answers,"
        " is_active, created_at, updated_at) VALUES ('q', :options, 1, 0, 1, 1, 1)",
        {"options": "not json"},
        error=OperationalError,
    )


async def test_a_poll_needs_at_least_two_options(factory: async_sessionmaker[AsyncSession]) -> None:
    await _reject(
        factory,
        "INSERT INTO polls (question, options_json, is_anonymous, allows_multiple_answers,"
        " is_active, created_at, updated_at) VALUES ('q', '[\"only\"]', 1, 0, 1, 1, 1)",
    )


async def test_a_poll_cannot_exceed_twelve_options(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _reject(
        factory,
        "INSERT INTO polls (question, options_json, is_anonymous, allows_multiple_answers,"
        " is_active, created_at, updated_at) VALUES ('q', :options, 1, 0, 1, 1, 1)",
        {"options": json.dumps([f"option {index}" for index in range(13)])},
    )


@pytest.mark.parametrize("column", ["is_anonymous", "allows_multiple_answers", "is_active"])
async def test_boolean_columns_reject_non_boolean_values(
    factory: async_sessionmaker[AsyncSession], column: str
) -> None:
    values: dict[str, int] = {"is_anonymous": 1, "allows_multiple_answers": 0, "is_active": 1}
    values[column] = 2

    await _reject(
        factory,
        "INSERT INTO polls (question, options_json, is_anonymous, allows_multiple_answers,"
        " is_active, created_at, updated_at) VALUES ('q', '[\"a\",\"b\"]',"
        " :is_anonymous, :allows_multiple_answers, :is_active, 1, 1)",
        values,
    )


async def test_schedule_timezone_must_not_be_empty(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _reject(
        factory,
        "INSERT INTO schedules (poll_id, kind, time_of_day, timezone, is_paused,"
        " created_at, updated_at) VALUES (:poll_id, 'daily', '09:00', '', 0, 1, 1)",
        {"poll_id": seed.poll_id},
    )


async def test_schedule_kind_is_constrained(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _reject(
        factory,
        "INSERT INTO schedules (poll_id, kind, time_of_day, timezone, is_paused,"
        " created_at, updated_at) VALUES (:poll_id, 'hourly', '09:00', 'UTC', 0, 1, 1)",
        {"poll_id": seed.poll_id},
    )


async def test_day_of_month_is_bounded(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _reject(
        factory,
        "INSERT INTO schedules (poll_id, kind, day_of_month, timezone, is_paused,"
        " created_at, updated_at) VALUES (:poll_id, 'monthly', 32, 'UTC', 0, 1, 1)",
        {"poll_id": seed.poll_id},
    )


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO deliveries (poll_id, chat_id, trigger, scheduled_for_utc, status,"
        " attempt_count, created_at) VALUES (:poll_id, :chat_id, 'scheduled', 1, 'unknown', 0, 1)",
        "INSERT INTO deliveries (poll_id, chat_id, trigger, scheduled_for_utc, status,"
        " attempt_count, created_at) VALUES (:poll_id, :chat_id, 'recurring', 1, 'pending', 0, 1)",
        "INSERT INTO deliveries (poll_id, chat_id, trigger, scheduled_for_utc, status,"
        " attempt_count, created_at) VALUES (:poll_id, :chat_id, 'scheduled', 1, 'pending', -1, 1)",
    ],
)
async def test_delivery_state_columns_are_constrained(
    seed: Seed, factory: async_sessionmaker[AsyncSession], statement: str
) -> None:
    await _reject(factory, statement, {"poll_id": seed.poll_id, "chat_id": seed.chat_id})


async def test_a_scheduled_occurrence_is_created_once(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    row = make_delivery(poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id)

    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        assert await repository.insert_many_ignore([row], now=FIXED_NOW) == 1
        assert await repository.insert_many_ignore([row], now=FIXED_NOW) == 0

    async with session_scope(factory) as session:
        stored = await DeliveryRepository(session).list_for_occurrence(
            seed.schedule_id, FIXED_NOW.replace(hour=9, minute=0)
        )
        assert len(stored) == 1


async def test_a_manual_delivery_may_repeat_a_scheduled_occurrence(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    scheduled = make_delivery(
        poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id
    )
    manual = Delivery(
        poll_id=seed.poll_id,
        chat_id=seed.chat_id,
        trigger=Trigger.MANUAL,
        status=DeliveryStatus.PENDING,
        scheduled_for=FIXED_NOW,
        schedule_id=None,
    )

    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        assert await repository.insert_many_ignore([scheduled, manual, manual], now=FIXED_NOW) == 3


async def test_deleting_a_poll_with_deliveries_is_restricted(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        await DeliveryRepository(session).insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id
                )
            ],
            now=FIXED_NOW,
        )

    await _reject(factory, "DELETE FROM polls WHERE id = :poll_id", {"poll_id": seed.poll_id})


async def test_deleting_a_chat_with_deliveries_is_restricted(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        await DeliveryRepository(session).insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id
                )
            ],
            now=FIXED_NOW,
        )

    await _reject(factory, "DELETE FROM chats WHERE id = :chat_id", {"chat_id": seed.chat_id})


async def test_deleting_a_schedule_keeps_the_delivery_history(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        await DeliveryRepository(session).insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id
                )
            ],
            now=FIXED_NOW,
        )

    async with session_scope(factory) as session:
        await session.execute(
            text("DELETE FROM schedules WHERE id = :schedule_id"),
            {"schedule_id": seed.schedule_id},
        )

    async with session_scope(factory) as session:
        rows = (
            await session.execute(
                text("SELECT schedule_id FROM deliveries WHERE poll_id = :poll_id"),
                {"poll_id": seed.poll_id},
            )
        ).all()

    assert rows == [(None,)]


async def test_audit_entity_type_is_constrained(factory: async_sessionmaker[AsyncSession]) -> None:
    await _reject(
        factory,
        "INSERT INTO audit_events (actor_user_id, action, entity_type, created_at)"
        " VALUES (1, 'poll.create', 'banana', 1)",
    )


async def test_audit_details_must_be_valid_json(factory: async_sessionmaker[AsyncSession]) -> None:
    await _reject(
        factory,
        "INSERT INTO audit_events (actor_user_id, action, entity_type, details_json, created_at)"
        " VALUES (1, 'poll.create', 'poll', 'not json', 1)",
    )


async def test_chat_titles_are_bounded(factory: async_sessionmaker[AsyncSession]) -> None:
    await _reject(
        factory,
        "INSERT INTO chats (telegram_chat_id, type, title, is_active, created_at, updated_at)"
        " VALUES (-1, 'group', :title, 1, 1, 1)",
        {"title": "t" * 256},
    )


async def test_rows_are_ordered_deterministically(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        repository = ChatRepository(session)
        for chat_id in (-300, -100, -200):
            await repository.upsert(make_chat(telegram_chat_id=chat_id), now=FIXED_NOW)

    async with session_scope(factory) as session:
        chats = await ChatRepository(session).list_all()

    assert [chat.telegram_chat_id for chat in chats] == [-300, -200, -100]
