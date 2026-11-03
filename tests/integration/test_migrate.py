"""Migration 0002 and backup behaviour (SPEC-003 FR-302)."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from telegram_auto_poll.db.engine import create_engine
from telegram_auto_poll.db.migrate import (
    BACKUP_PREFIX,
    alembic_config,
    backup_database,
    current_revision,
    migrate_on_startup,
    prune_backups,
    upgrade_database,
)
from telegram_auto_poll.db.models import Base
from tests.factories import FIXED_NOW


@pytest_asyncio.fixture
async def bare_engine(database_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(f"sqlite+aiosqlite:///{database_path}")
    try:
        yield engine
    finally:
        await engine.dispose()


def _make_backups(directory: Path, count: int) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for offset in range(count):
        stamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=offset)
        path = directory / f"{BACKUP_PREFIX}{stamp.strftime('%Y%m%dT%H%M%S')}.db"
        path.write_bytes(b"SQLite format 3\x00")
        paths.append(path)
    return paths


def test_backup_is_skipped_for_a_fresh_install(tmp_path: Path) -> None:
    assert (
        backup_database(
            db_path=tmp_path / "missing.db", backup_dir=tmp_path / "backups", now=FIXED_NOW
        )
        is None
    )


def test_backup_copies_with_restricted_permissions(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker (value) VALUES ('kept')")

    backup = backup_database(db_path=source, backup_dir=tmp_path / "backups", now=FIXED_NOW)

    assert backup is not None
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT value FROM marker").fetchall() == [("kept",)]


def test_prune_keeps_the_newest_and_handles_missing(tmp_path: Path) -> None:
    created = _make_backups(tmp_path / "backups", 8)
    assert prune_backups(tmp_path / "backups", keep=5) == tuple(created[:3])
    assert prune_backups(tmp_path / "absent") == ()


async def test_current_revision_and_fresh_migration(
    bare_engine: AsyncEngine, database_path: Path
) -> None:
    assert await current_revision(bare_engine) is None
    await upgrade_database(bare_engine)
    assert await current_revision(bare_engine) == "0002"


async def test_migrate_on_startup_is_idempotent(
    bare_engine: AsyncEngine, database_path: Path, tmp_path: Path
) -> None:
    first = await migrate_on_startup(
        engine=bare_engine,
        db_path=database_path,
        backup_dir=tmp_path / "backups",
        now=FIXED_NOW,
    )
    second = await migrate_on_startup(
        engine=bare_engine,
        db_path=database_path,
        backup_dir=tmp_path / "backups",
        now=FIXED_NOW + timedelta(hours=1),
    )

    assert first.backup is None
    assert second.backup is not None
    assert await current_revision(bare_engine) == "0002"


async def test_destructive_guard_aborts_then_allows_with_the_flag(
    bare_engine: AsyncEngine, database_path: Path, tmp_path: Path
) -> None:
    async with bare_engine.begin() as connection:
        await connection.run_sync(lambda sync: command.upgrade(alembic_config(sync), "0001"))
        await connection.execute(
            text(
                "INSERT INTO polls (question, options_json, is_anonymous, "
                "allows_multiple_answers, is_active, created_at, updated_at) "
                "VALUES ('q', '[\"a\",\"b\"]', 1, 0, 1, 1, 1)"
            )
        )

    with pytest.raises(RuntimeError, match="refusing destructive"):
        await migrate_on_startup(
            engine=bare_engine,
            db_path=database_path,
            backup_dir=tmp_path / "backups",
            now=FIXED_NOW,
            allow_destructive=False,
        )

    await migrate_on_startup(
        engine=bare_engine,
        db_path=database_path,
        backup_dir=tmp_path / "backups",
        now=FIXED_NOW,
        allow_destructive=True,
    )
    assert await current_revision(bare_engine) == "0002"


async def test_schema_matches_the_models(engine: AsyncEngine) -> None:
    def diff(connection: object) -> list[object]:
        context = MigrationContext.configure(connection)  # type: ignore[arg-type]
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as connection:
        differences = await connection.run_sync(diff)

    assert differences == []
