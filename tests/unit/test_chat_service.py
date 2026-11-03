"""Chat registration and deactivation (SPEC-003 FR-112 - FR-115)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.poll_messages import PollMessageRepository
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import ChatType
from telegram_auto_poll.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from telegram_auto_poll.services.chat_service import ChatService, parse_chat_id
from tests.conftest import Seed
from tests.factories import FIXED_NOW
from tests.fakes import FakeChatGateway, FakeClock

CHAT_ID = -1_001_234_567_890
BOT_ID = 123456789


@pytest.fixture
def gateway() -> FakeChatGateway:
    return FakeChatGateway()


@pytest.fixture
def service(factory: async_sessionmaker[AsyncSession], gateway: FakeChatGateway) -> ChatService:
    return ChatService(
        uow=lambda: unit_of_work(factory),
        gateway=gateway,
        clock=FakeClock(FIXED_NOW),
        bot_user_id=BOT_ID,
    )


def test_parse_chat_id_accepts_negative_and_rejects_positive() -> None:
    assert parse_chat_id(" -1001234567890 ") == -1_001_234_567_890
    with pytest.raises(ValidationError, match="paste the numeric chat id"):
        parse_chat_id("123")


async def test_register_stores_and_reactivates(
    service: ChatService, gateway: FakeChatGateway
) -> None:
    gateway.add_chat(CHAT_ID, title="Test group")
    gateway.members[(CHAT_ID, BOT_ID)] = True

    first = await service.register(CHAT_ID, actor_user_id=1)
    assert first.chat.title == "Test group"
    assert first.reactivated is False
    assert first.warning is None

    await service.deactivate(first.chat.id or 0, actor_user_id=1)
    second = await service.register(CHAT_ID, actor_user_id=1)
    assert second.reactivated is True


async def test_register_channel_requires_post_permission(
    service: ChatService, gateway: FakeChatGateway
) -> None:
    gateway.add_chat(CHAT_ID, chat_type=ChatType.CHANNEL, title="Channel")
    gateway.members[(CHAT_ID, BOT_ID)] = False

    with pytest.raises(PermissionDeniedError, match="Post messages"):
        await service.register(CHAT_ID, actor_user_id=1)


async def test_register_unknown_chat_raises(service: ChatService, gateway: FakeChatGateway) -> None:
    gateway.missing.add(CHAT_ID)
    with pytest.raises(NotFoundError, match="does not expose"):
        await service.register(CHAT_ID, actor_user_id=1)


async def test_deactivate_skips_pending_messages_and_is_idempotent(
    service: ChatService,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await PollMessageRepository(session).reset_for_retry(
            seed.message_id, next_attempt_at=FIXED_NOW, now=FIXED_NOW
        )

    first = await service.deactivate(seed.chat_id, actor_user_id=1)
    second = await service.deactivate(seed.chat_id, actor_user_id=1)

    assert first.skipped_messages == 1
    assert first.chat.is_active is False
    assert second.skipped_messages == 0


async def test_deactivate_unknown_chat_raises(service: ChatService) -> None:
    with pytest.raises(NotFoundError, match="is not registered"):
        await service.deactivate(999, actor_user_id=1)


async def test_page_lists_chats(service: ChatService, seed: Seed) -> None:
    page = await service.page(1)
    assert page.total == 1
    assert page.items[0].id == seed.chat_id
