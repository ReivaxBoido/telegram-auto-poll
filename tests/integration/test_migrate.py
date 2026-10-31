"""Startup migration: backup ordering, retention, idempotency, and schema drift.

The order is the safety property: a backup must exist on disk *before* Alembic touches the schema,
and a failed upgrade must leave that backup behind.
"""

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
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from telegram_auto_poll.db import migrate
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
    """An engine over a database file on which no migration has run."""
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
    backup_dir = tmp_path / "backups"

    assert (
        backup_database(db_path=tmp_path / "missing.db", backup_dir=backup_dir, now=FIXED_NOW)
        is None
    )
    assert not backup_dir.exists()


def test_backup_copies_the_database_with_restricted_permissions(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker (value) VALUES ('kept')")
    backup_dir = tmp_path / "backups"

    backup = backup_database(db_path=source, backup_dir=backup_dir, now=FIXED_NOW)

    assert backup is not None
    assert backup.name == f"{BACKUP_PREFIX}20260301T120000.db"
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT value FROM marker").fetchall() == [("kept",)]


def test_prune_keeps_the_newest_backups(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    created = _make_backups(backup_dir, 8)

    pruned = prune_backups(backup_dir, keep=5)

    assert pruned == tuple(created[:3])
    assert sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.db")) == created[3:]


def test_prune_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert prune_backups(tmp_path / "absent") == ()


def test_prune_keeps_everything_when_under_the_limit(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    created = _make_backups(backup_dir, 2)

    assert prune_backups(backup_dir, keep=5) == ()
    assert sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.db")) == created


async def test_current_revision_is_none_before_the_first_migration(
    bare_engine: AsyncEngine,
) -> None:
    assert await current_revision(bare_engine) is None


async def test_migrate_on_startup_creates_the_schema_for_a_fresh_install(
    bare_engine: AsyncEngine, database_path: Path, tmp_path: Path
) -> None:
    result = await migrate_on_startup(
        engine=bare_engine,
        db_path=database_path,
        backup_dir=tmp_path / "backups",
        now=FIXED_NOW,
    )

    assert result.backup is None
    assert result.pruned == ()
    assert await current_revision(bare_engine) == "0001"


async def test_migrate_on_startup_backs_up_before_upgrading(
    bare_engine: AsyncEngine,
    database_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await upgrade_database(bare_engine)
    backup_dir = tmp_path / "backups"
    observed: list[list[Path]] = []

    async def failing_upgrade(engine: AsyncEngine) -> None:
        observed.append(sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.db")))
        raise RuntimeError("upgrade exploded")

    monkeypatch.setattr(migrate, "upgrade_database", failing_upgrade)

    with pytest.raises(RuntimeError, match="upgrade exploded"):
        await migrate_on_startup(
            engine=bare_engine, db_path=database_path, backup_dir=backup_dir, now=FIXED_NOW
        )

    assert len(observed) == 1, "upgrade must have been attempted exactly once"
    assert len(observed[0]) == 1, "the backup must exist before Alembic runs"
    assert len(list(backup_dir.glob(f"{BACKUP_PREFIX}*.db"))) == 1


async def test_migrate_on_startup_is_idempotent(
    bare_engine: AsyncEngine, database_path: Path, tmp_path: Path
) -> None:
    backup_dir = tmp_path / "backups"
    first = await migrate_on_startup(
        engine=bare_engine, db_path=database_path, backup_dir=backup_dir, now=FIXED_NOW
    )
    second = await migrate_on_startup(
        engine=bare_engine,
        db_path=database_path,
        backup_dir=backup_dir,
        now=FIXED_NOW + timedelta(hours=1),
    )

    assert first.backup is None
    assert second.backup is not None
    assert await current_revision(bare_engine) == "0001"


async def test_migrate_on_startup_prunes_old_backups(
    bare_engine: AsyncEngine, database_path: Path, tmp_path: Path
) -> None:
    backup_dir = tmp_path / "backups"
    await upgrade_database(bare_engine)
    _make_backups(backup_dir, 5)

    result = await migrate_on_startup(
        engine=bare_engine, db_path=database_path, backup_dir=backup_dir, now=FIXED_NOW
    )

    assert len(result.pruned) == 1
    assert len(list(backup_dir.glob(f"{BACKUP_PREFIX}*.db"))) == 5


async def test_schema_matches_the_models(engine: AsyncEngine) -> None:
    def diff(connection: object) -> list[object]:
        context = MigrationContext.configure(connection)  # type: ignore[arg-type]
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as connection:
        differences = await connection.run_sync(diff)

    assert differences == []


async def test_downgrade_base_drops_the_application_tables(engine: AsyncEngine) -> None:
    def downgrade(connection: object) -> None:
        command.downgrade(alembic_config(connection), "base")  # type: ignore[arg-type]

    async with engine.begin() as connection:
        await connection.run_sync(downgrade)

    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))

    assert tables == {"alembic_version"}
