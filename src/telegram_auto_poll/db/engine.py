"""Async SQLite engine, connection PRAGMAs, and session plumbing (SPEC-001 FR-102)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

#: Applied to every new DBAPI connection. ``foreign_keys`` is off by default in SQLite; WAL keeps
#: readers from blocking the single writer; NORMAL is the durability/speed trade-off the constitution
#: mandates for a single-VPS deployment.
BASE_PRAGMAS = (
    "PRAGMA foreign_keys=ON",
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
)

HEALTH_TIMEOUT_SECONDS = 1.0


def create_engine(database_url: str, *, busy_timeout_ms: int = 5000) -> AsyncEngine:
    """Create an async engine that applies the mandated PRAGMAs on every connection."""
    engine = create_async_engine(database_url, future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _apply_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            for pragma in BASE_PRAGMAS:
                cursor.execute(pragma)
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory that never expires attributes on commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Run one unit of work: commit on success, roll back on any exception, always close."""
    session = factory()
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_database(factory: async_sessionmaker[AsyncSession]) -> bool:
    """Return whether ``SELECT 1`` completes within the health timeout."""
    try:
        async with asyncio.timeout(HEALTH_TIMEOUT_SECONDS):
            async with factory() as session:
                await session.execute(text("SELECT 1"))
    except (TimeoutError, OSError, SQLAlchemyError):
        return False
    return True
