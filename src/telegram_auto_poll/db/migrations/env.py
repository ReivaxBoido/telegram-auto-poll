"""Alembic environment.

The application always migrates through :mod:`telegram_auto_poll.db.migrate`, which hands an open
sync connection to Alembic via ``config.attributes``. Requiring that connection keeps this file free
of environment branches and keeps migration ordering under the caller's control.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from telegram_auto_poll.db.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection: Connection = context.config.attributes["connection"]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
