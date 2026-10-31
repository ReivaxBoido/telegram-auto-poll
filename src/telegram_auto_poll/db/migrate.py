"""Startup migration with a pre-migration backup (SPEC-001 FR-102, FR-105).

Order matters and is tested: backup first, then ``alembic upgrade head``, then prune old backups. If
the upgrade fails the backup is left in place and the exception propagates, so the process exits
non-zero without touching the schema.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
BACKUP_PREFIX = "pre-migrate-"
BACKUP_KEEP = 5


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """What a startup migration did, for logging."""

    backup: Path | None
    pruned: tuple[Path, ...]


def backup_database(*, db_path: Path, backup_dir: Path, now: datetime) -> Path | None:
    """Copy ``db_path`` into ``backup_dir`` with mode 0600. Returns ``None`` for a fresh install."""
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S")
    target = backup_dir / f"{BACKUP_PREFIX}{stamp}.db"
    with sqlite3.connect(db_path) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    target.chmod(0o600)
    return target


def prune_backups(backup_dir: Path, *, keep: int = BACKUP_KEEP) -> tuple[Path, ...]:
    """Delete all but the ``keep`` newest pre-migration backups."""
    if not backup_dir.exists():
        return ()
    backups = sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.db"))
    stale = backups[: max(len(backups) - keep, 0)]
    for path in stale:
        path.unlink()
    return tuple(stale)


def alembic_config(connection: Connection) -> Config:
    """Build an Alembic config bound to an open synchronous connection."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    return config


async def upgrade_database(engine: AsyncEngine) -> None:
    """Apply every migration up to ``head``."""
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: command.upgrade(alembic_config(sync_connection), "head")
        )


async def migrate_on_startup(
    *,
    engine: AsyncEngine,
    db_path: Path,
    backup_dir: Path,
    now: datetime,
) -> MigrationResult:
    """Back up, migrate, and prune. Callers log the result and abort on any exception."""
    backup = backup_database(db_path=db_path, backup_dir=backup_dir, now=now)
    await upgrade_database(engine)
    return MigrationResult(backup=backup, pruned=prune_backups(backup_dir))


async def current_revision(engine: AsyncEngine) -> str | None:
    """Return the applied revision, or ``None`` when the database has never been migrated."""
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            return result.scalar()
    except OperationalError:
        return None
