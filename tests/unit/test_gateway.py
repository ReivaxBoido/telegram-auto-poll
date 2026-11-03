"""Telegram adapters (SPEC-003 FR-329, FR-333)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

from telegram_auto_poll.bot.telegram_gateway import (
    TelegramChatGateway,
    TelegramMessageGateway,
    forwarded_chat_id,
)
from telegram_auto_poll.domain.enums import ChatType
from telegram_auto_poll.domain.errors import DeliveryError, NotFoundError, PermissionDeniedError


class RaisingBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> object:
        if self.error is not None:
            raise self.error
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=77)

    async def edit_message_text(self, **kwargs: Any) -> object:
        if self.error is not None:
            raise self.error
        self.edited.append(kwargs)
        return True

    async def get_chat(self, chat_id: int) -> object:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(id=chat_id, type="supergroup", title="Test")

    async def get_chat_member(self, chat_id: int, user_id: int) -> object:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status="administrator", can_post_messages=True)


async def test_send_message_returns_id_and_forwards_keyboard() -> None:
    bot = RaisingBot()
    gateway = TelegramMessageGateway(bot)  # type: ignore[arg-type]

    message_id = await gateway.send_message(1, "hello", keyboard=((("In", "v1:v:toggle:1_1"),),))

    assert message_id == 77
    assert bot.sent[0]["text"] == "hello"
    assert bot.sent[0]["reply_markup"] is not None


@pytest.mark.parametrize(
    ("error", "retryable", "classification"),
    [
        (RetryAfter(5), True, "retry_after"),
        (BadRequest("bad"), False, "bad_request"),
        (Forbidden("no"), False, "forbidden"),
        (TimedOut(), True, "timeout"),
        (NetworkError("net"), True, "network"),
        (TelegramError("other"), False, "telegram"),
    ],
)
async def test_send_message_classifies_errors(
    error: Exception, retryable: bool, classification: str
) -> None:
    gateway = TelegramMessageGateway(RaisingBot(error))  # type: ignore[arg-type]

    with pytest.raises(DeliveryError) as caught:
        await gateway.send_message(1, "hello")

    assert caught.value.retryable is retryable
    assert caught.value.classification == classification


async def test_edit_message_success_and_not_modified() -> None:
    bot = RaisingBot()
    gateway = TelegramMessageGateway(bot)  # type: ignore[arg-type]
    await gateway.edit_message(text="x", chat_id=1, message_id=2, keyboard=None)
    assert bot.edited[0]["chat_id"] == 1

    not_modified = TelegramMessageGateway(
        RaisingBot(BadRequest("Message is not modified"))  # type: ignore[arg-type]
    )
    await not_modified.edit_message(text="x", chat_id=1, message_id=2)


@pytest.mark.parametrize(
    ("error", "classification"),
    [
        (BadRequest("message to edit not found"), "message_not_found"),
        (BadRequest("message can't be edited"), "message_not_found"),
        (BadRequest("other"), "bad_request"),
        (RetryAfter(7), "retry_after"),
        (Forbidden("no"), "forbidden"),
        (TimedOut(), "timeout"),
        (NetworkError("net"), "network"),
        (TelegramError("other"), "telegram"),
    ],
)
async def test_edit_message_classifies_errors(error: Exception, classification: str) -> None:
    gateway = TelegramMessageGateway(RaisingBot(error))  # type: ignore[arg-type]

    with pytest.raises(DeliveryError) as caught:
        await gateway.edit_message(text="x", inline_message_id="inline-1")

    assert caught.value.classification == classification


async def test_chat_gateway_reads_metadata_and_membership() -> None:
    gateway = TelegramChatGateway(RaisingBot())  # type: ignore[arg-type]

    info = await gateway.get_chat(-1)
    assert info.type is ChatType.SUPERGROUP
    assert await gateway.get_member(-1, 1) is True


async def test_chat_gateway_maps_errors() -> None:
    with pytest.raises(NotFoundError):
        await TelegramChatGateway(RaisingBot(TelegramError("no"))).get_chat(-1)  # type: ignore[arg-type]
    with pytest.raises(PermissionDeniedError):
        await TelegramChatGateway(RaisingBot(TelegramError("no"))).get_member(-1, 1)  # type: ignore[arg-type]


def test_forwarded_chat_id_supports_both_shapes() -> None:
    assert forwarded_chat_id(SimpleNamespace(forward_origin=None, forward_from_chat=None)) is None
    assert (
        forwarded_chat_id(
            SimpleNamespace(
                forward_origin=SimpleNamespace(sender_chat=SimpleNamespace(id=-1)),
                forward_from_chat=None,
            )
        )
        == -1
    )
    assert (
        forwarded_chat_id(
            SimpleNamespace(
                forward_origin=SimpleNamespace(sender_chat=None, chat=SimpleNamespace(id=-2)),
                forward_from_chat=None,
            )
        )
        == -2
    )
    assert (
        forwarded_chat_id(
            SimpleNamespace(
                forward_origin=None,
                forward_from_chat=SimpleNamespace(id=-3),
            )
        )
        == -3
    )


def test_retry_after_accepts_a_timedelta() -> None:
    from datetime import timedelta

    from telegram_auto_poll.bot.telegram_gateway import _retry_after_seconds

    assert _retry_after_seconds(timedelta(seconds=5)) == 5.0


def test_forwarded_chat_id_falls_through_an_empty_origin() -> None:
    message = SimpleNamespace(
        forward_origin=SimpleNamespace(sender_chat=None, chat=None),
        forward_from_chat=None,
    )
    assert forwarded_chat_id(message) is None


async def test_chat_gateway_rejects_private_chats() -> None:
    class PrivateBot:
        async def get_chat(self, chat_id: int) -> object:
            return SimpleNamespace(id=chat_id, type="private", title="")

    with pytest.raises(Exception, match="only groups"):
        await TelegramChatGateway(PrivateBot()).get_chat(1)  # type: ignore[arg-type]


async def test_chat_gateway_member_statuses() -> None:
    class RestrictedBot:
        async def get_chat_member(self, chat_id: int, user_id: int) -> object:
            return SimpleNamespace(status="restricted", can_send_messages=True)

    class MemberBot:
        async def get_chat_member(self, chat_id: int, user_id: int) -> object:
            return SimpleNamespace(status="member")

    assert await TelegramChatGateway(RestrictedBot()).get_member(1, 2) is True  # type: ignore[arg-type]
    assert await TelegramChatGateway(MemberBot()).get_member(1, 2) is False  # type: ignore[arg-type]


async def test_edit_without_a_keyboard_removes_it() -> None:
    bot = RaisingBot()
    gateway = TelegramMessageGateway(bot)  # type: ignore[arg-type]

    await gateway.edit_message(text="closed", chat_id=1, message_id=2)

    markup = bot.edited[0]["reply_markup"]
    assert markup.inline_keyboard == ()
