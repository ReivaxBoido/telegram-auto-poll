"""Telegram chat gateway and forwarded-message extraction (FR-112, FR-113)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from telegram.error import BadRequest, TelegramError

from telegram_auto_poll.bot.telegram_gateway import TelegramChatGateway, forwarded_chat_id
from telegram_auto_poll.domain.enums import ChatType
from telegram_auto_poll.domain.errors import NotFoundError, PermissionDeniedError, ValidationError

CHAT_ID = -1_001_234_567_890


class FakeBot:
    def __init__(
        self,
        chat: SimpleNamespace | None = None,
        member: SimpleNamespace | None = None,
        error: Exception | None = None,
    ) -> None:
        self.chat = chat
        self.member = member
        self.error = error
        self.calls: list[tuple[Any, ...]] = []

    async def get_chat(self, chat_id: int) -> SimpleNamespace:
        self.calls.append(("get_chat", chat_id))
        if self.error is not None:
            raise self.error
        assert self.chat is not None
        return self.chat

    async def get_chat_member(self, chat_id: int, user_id: int) -> SimpleNamespace:
        self.calls.append(("get_chat_member", chat_id, user_id))
        if self.error is not None:
            raise self.error
        assert self.member is not None
        return self.member


def chat(chat_type: str = "supergroup", title: str = "Ops") -> SimpleNamespace:
    return SimpleNamespace(id=CHAT_ID, type=chat_type, title=title)


def member(status: str, **rights: bool) -> SimpleNamespace:
    return SimpleNamespace(status=status, **rights)


async def test_get_chat_maps_the_bot_api_shape() -> None:
    gateway = TelegramChatGateway(FakeBot(chat=chat()))

    info = await gateway.get_chat(CHAT_ID)

    assert info.chat_id == CHAT_ID
    assert info.type is ChatType.SUPERGROUP
    assert info.title == "Ops"


async def test_get_chat_accepts_a_channel_without_a_title() -> None:
    gateway = TelegramChatGateway(FakeBot(chat=chat("channel", title=None)))

    info = await gateway.get_chat(CHAT_ID)

    assert info.type is ChatType.CHANNEL
    assert info.title == ""


async def test_get_chat_rejects_a_private_chat() -> None:
    gateway = TelegramChatGateway(FakeBot(chat=chat("private", "DM")))

    with pytest.raises(ValidationError) as exc_info:
        await gateway.get_chat(CHAT_ID)

    assert exc_info.value.field == "chat_type"


async def test_get_chat_maps_api_errors_to_not_found() -> None:
    gateway = TelegramChatGateway(FakeBot(error=BadRequest("chat not found")))

    with pytest.raises(NotFoundError):
        await gateway.get_chat(CHAT_ID)


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (member("administrator", can_post_messages=True), True),
        (member("administrator"), True),
        (member("creator"), True),
        (member("restricted", can_send_messages=True), True),
        (member("restricted", can_send_messages=False), False),
        (member("member"), False),
        (member("left"), False),
    ],
)
async def test_get_member_reports_posting_rights(member: SimpleNamespace, expected: bool) -> None:
    gateway = TelegramChatGateway(FakeBot(member=member))

    assert await gateway.get_member(CHAT_ID, 777) is expected


async def test_get_member_maps_api_errors_to_permission_denied() -> None:
    gateway = TelegramChatGateway(FakeBot(error=TelegramError("forbidden")))

    with pytest.raises(PermissionDeniedError):
        await gateway.get_member(CHAT_ID, 777)


def test_forwarded_chat_prefers_the_new_sender_chat() -> None:
    message = SimpleNamespace(
        forward_origin=SimpleNamespace(sender_chat=SimpleNamespace(id=CHAT_ID)),
        forward_from_chat=SimpleNamespace(id=-999),
    )

    assert forwarded_chat_id(message) == CHAT_ID


def test_forwarded_chat_falls_back_to_the_origin_chat() -> None:
    message = SimpleNamespace(
        forward_origin=SimpleNamespace(chat=SimpleNamespace(id=CHAT_ID)),
    )

    assert forwarded_chat_id(message) == CHAT_ID


def test_forwarded_chat_supports_the_legacy_attribute() -> None:
    message = SimpleNamespace(forward_from_chat=SimpleNamespace(id=CHAT_ID))

    assert forwarded_chat_id(message) == CHAT_ID


def test_forwarded_chat_returns_none_for_an_origin_without_a_chat() -> None:
    message = SimpleNamespace(forward_origin=SimpleNamespace(sender_chat=None, chat=None))

    assert forwarded_chat_id(message) is None


def test_forwarded_chat_returns_none_for_an_ordinary_message() -> None:
    assert forwarded_chat_id(SimpleNamespace(text="hello")) is None
