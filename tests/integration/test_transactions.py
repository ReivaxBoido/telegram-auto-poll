"""Transaction boundaries: services own the unit of work, repositories never commit.

These tests fail if a repository starts calling ``commit()`` or if a rollback stops removing the
audit row written alongside a mutation (FR-134).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import (
    check_database,
    create_engine,
    create_session_factory,
    session_scope,
)
from telegram_auto_poll.db.repositories.audit import AuditRepository
from telegram_auto_poll.db.repositories.chats import ChatRepository
from telegram_auto_poll.domain.enums import AuditAction
from tests.factories import FIXED_NOW, make_audit_event, make_chat

REPOSITORIES = (
    Path(__file__).resolve().parents[2] / "src" / "telegram_auto_poll" / "db" / "repositories"
)


async def test_session_scope_commits_on_success(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await ChatRepository(session).upsert(make_chat(), now=FIXED_NOW)

    async with session_scope(factory) as session:
        assert await ChatRepository(session).count() == 1


async def test_session_scope_rolls_back_on_any_exception(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with session_scope(factory) as session:
            await AuditRepository(session).add(make_audit_event(), now=FIXED_NOW)
            raise RuntimeError("boom")

    async with session_scope(factory) as session:
        assert await AuditRepository(session).list_recent() == []


async def test_rollback_removes_the_chat_and_its_audit_row_together(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(RuntimeError):
        async with session_scope(factory) as session:
            chat = await ChatRepository(session).upsert(make_chat(), now=FIXED_NOW)
            await AuditRepository(session).add(
                make_audit_event(action=AuditAction.CHAT_REGISTER, entity_id=chat.id or 0),
                now=FIXED_NOW,
            )
            raise RuntimeError("abort")

    async with session_scope(factory) as session:
        assert await ChatRepository(session).count() == 0
        assert await AuditRepository(session).list_recent() == []


async def test_session_scope_returns_the_connection_to_the_pool(
    engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        await ChatRepository(session).upsert(make_chat(), now=FIXED_NOW)

    assert engine.pool.checkedout() == 0


def test_repositories_never_commit() -> None:
    offenders = sorted(
        path.name
        for path in REPOSITORIES.glob("*.py")
        if ".commit(" in path.read_text(encoding="utf-8")
    )

    assert offenders == []


def test_repositories_never_roll_back() -> None:
    offenders = sorted(
        path.name
        for path in REPOSITORIES.glob("*.py")
        if ".rollback(" in path.read_text(encoding="utf-8")
    )

    assert offenders == []


async def test_check_database_succeeds_against_a_migrated_database(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    assert await check_database(factory) is True


async def test_check_database_reports_a_broken_path(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'absent' / 'db.sqlite'}")
    factory = create_session_factory(engine)
    try:
        assert await check_database(factory) is False
    finally:
        await engine.dispose()


async def test_a_failed_statement_does_not_poison_the_next_transaction(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(Exception, match="no such table"):
        async with session_scope(factory) as session:
            await session.execute(text("SELECT * FROM table_that_does_not_exist"))

    async with session_scope(factory) as session:
        assert await ChatRepository(session).count() == 0
