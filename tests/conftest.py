"""Shared fixtures and global test guards.

Unit and integration tests use the same temporary SQLite file and the real migrations; there is no
in-memory database shortcut, so foreign keys and WAL behave exactly as they do in production.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import create_engine, create_session_factory, session_scope
from telegram_auto_poll.db.migrate import upgrade_database
from telegram_auto_poll.db.repositories.chats import ChatRepository
from telegram_auto_poll.db.repositories.poll_instances import PollInstanceRepository
from telegram_auto_poll.db.repositories.poll_messages import PollMessageRepository
from telegram_auto_poll.db.repositories.polls import PollRepository
from telegram_auto_poll.domain.entities import Chat, Poll, PollInstance, PollMessage
from tests.factories import FIXED_NOW, make_chat, make_instance, make_poll


def _blocked(*args: object, **kwargs: object) -> None:
    raise RuntimeError("network access is blocked in tests (NFR-304)")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Block outbound I/O while leaving AF_UNIX loop internals alone."""
    if request.node.get_closest_marker("allow_network") is not None:
        return
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A writable stand-in for the container's /data volume."""
    directory = tmp_path / "data"
    directory.mkdir()
    return directory


ADMIN_USER_ID = 100_000_001
FAKE_BOT_TOKEN = "123456789:TEST-TOKEN-DO-NOT-USE-abcdefghijklmnop"

CONFIG_ENV_VARS = (
    "ADMIN_COMMANDS_PER_MINUTE",
    "ADMIN_USER_IDS",
    "ALLOW_DESTRUCTIVE_MIGRATION",
    "BOT_POLL_TIMEOUT_SECONDS",
    "BUILD_SHA",
    "BUILD_VERSION",
    "CLOCK_SKEW_WARNING_SECONDS",
    "DATABASE_URL",
    "DATA_DIR",
    "DB_BUSY_TIMEOUT_MS",
    "DEFAULT_TIMEZONE",
    "ENVIRONMENT",
    "HEALTH_HOST",
    "HEALTH_PORT",
    "LOG_FORMAT",
    "LOG_LEVEL",
    "MESSAGE_EDIT_CONCURRENCY",
    "MESSAGE_EDIT_RETRY_ATTEMPTS",
    "METRICS_ENABLED",
    "MIGRATE_ON_STARTUP",
    "POSTING_TICK_SECONDS",
    "RESPONSE_NAMES_PER_OPTION",
    "RESPONSE_RETENTION_DAYS",
    "RESPONSE_UPDATE_DEBOUNCE_SECONDS",
    "RETENTION_DAYS",
    "SCHEDULER_TICK_SECONDS",
    "SEND_CONCURRENCY",
    "SEND_GRACE_SECONDS",
    "SEND_RETRY_ATTEMPTS",
    "SEND_RETRY_BASE_SECONDS",
    "SHUTDOWN_GRACE_SECONDS",
    "TELEGRAM_BOT_TOKEN",
    "VOTES_PER_MINUTE",
    "WIZARD_TTL_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@dataclass(frozen=True)
class Seed:
    """Identifiers of one chat, poll, instance, and message that reference each other."""

    chat: Chat
    poll: Poll
    instance: PollInstance
    message: PollMessage

    @property
    def chat_id(self) -> int:
        assert self.chat.id is not None
        return self.chat.id

    @property
    def poll_id(self) -> int:
        assert self.poll.id is not None
        return self.poll.id

    @property
    def instance_id(self) -> int:
        assert self.instance.id is not None
        return self.instance.id

    @property
    def message_id(self) -> int:
        assert self.message.id is not None
        return self.message.id


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "telegram-auto-poll.db"


@pytest_asyncio.fixture
async def engine(database_path: Path) -> AsyncIterator[AsyncEngine]:
    """A file-backed engine with every migration applied."""
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_engine(url, busy_timeout_ms=1000)
    await upgrade_database(engine)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest_asyncio.fixture
async def seed(factory: async_sessionmaker[AsyncSession]) -> Seed:
    """Create the chat/poll/instance/message quadruple most persistence tests need."""
    async with session_scope(factory) as session:
        chat = await ChatRepository(session).upsert(make_chat(), now=FIXED_NOW)
        poll = await PollRepository(session).add(make_poll(), now=FIXED_NOW)
        instance = await PollInstanceRepository(session).add(
            make_instance(poll_id=poll.id), now=FIXED_NOW
        )
        messages = await PollMessageRepository(session).add_many(
            instance.id or 0, [chat.id or 0], now=FIXED_NOW
        )
    async with session_scope(factory) as session:
        repo = PollMessageRepository(session)
        await repo.mark_sent(messages[0].id or 0, 500, now=FIXED_NOW)
        message = await repo.get(messages[0].id or 0)
    assert message is not None
    return Seed(chat=chat, poll=poll, instance=instance, message=message)
