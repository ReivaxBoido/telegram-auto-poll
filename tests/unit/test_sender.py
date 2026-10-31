"""Telegram sender adapter (FR-120, FR-129, FR-131) - fake Bot, no network."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

from telegram_auto_poll.bot.telegram_sender import TelegramPollSender, _retry_after_seconds
from telegram_auto_poll.domain.entities import PollRequest
from telegram_auto_poll.domain.errors import DeliveryError


class FakeMessage:
    message_id = 4242


class FakeBot:
    """Records ``send_poll`` kwargs and raises whatever the test queued."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def send_poll(self, **kwargs: Any) -> FakeMessage:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return FakeMessage()


def request(**overrides: Any) -> PollRequest:
    defaults: dict[str, Any] = {
        "chat_id": -1_001_234_567_890,
        "question": "What should we ship next?",
        "options": ("Reliability", "Speed"),
        "description": "Pick one.",
        "is_anonymous": True,
        "allows_multiple_answers": False,
    }
    return PollRequest(**{**defaults, **overrides})


async def test_success_returns_the_message_id_and_sends_exact_fields() -> None:
    bot = FakeBot()

    message_id = await TelegramPollSender(bot).send_poll(request())

    assert message_id == 4242
    assert bot.calls == [
        {
            "chat_id": -1_001_234_567_890,
            "question": "What should we ship next?",
            "options": ["Reliability", "Speed"],
            "is_anonymous": True,
            "allows_multiple_answers": False,
            "type": "regular",
            "description": "Pick one.",
        }
    ]


async def test_no_parse_mode_is_ever_sent() -> None:
    bot = FakeBot()

    await TelegramPollSender(bot).send_poll(request(question="<b>literal</b>"))

    assert "parse_mode" not in bot.calls[0]
    assert bot.calls[0]["question"] == "<b>literal</b>"


async def test_description_is_omitted_when_absent() -> None:
    bot = FakeBot()

    await TelegramPollSender(bot).send_poll(request(description=None))

    assert "description" not in bot.calls[0]


@pytest.mark.parametrize(
    ("error", "retryable", "retry_after", "classification"),
    [
        (RetryAfter(7), True, 7.0, "retry_after"),
        (TimedOut(), True, None, "timeout"),
        (NetworkError("connection reset"), True, None, "network"),
        (BadRequest("chat not found"), False, None, "bad_request"),
        (Forbidden("bot was kicked"), False, None, "forbidden"),
        (TelegramError("internal"), False, None, "telegram"),
    ],
)
async def test_errors_are_classified_for_the_retry_policy(
    error: Exception,
    retryable: bool,
    retry_after: float | None,
    classification: str,
) -> None:
    sender = TelegramPollSender(FakeBot(error))

    with pytest.raises(DeliveryError) as exc_info:
        await sender.send_poll(request())

    assert exc_info.value.retryable is retryable
    assert exc_info.value.retry_after == retry_after
    assert exc_info.value.classification == classification


async def test_retry_after_may_be_a_timedelta() -> None:
    sender = TelegramPollSender(FakeBot(RetryAfter(timedelta(seconds=9))))

    with pytest.raises(DeliveryError) as exc_info:
        await sender.send_poll(request())

    assert exc_info.value.retry_after == 9.0


def test_retry_after_seconds_accepts_both_api_shapes() -> None:
    # python-telegram-bot currently returns an int but will return a timedelta in a future major
    # version, so the adapter accepts both.
    assert _retry_after_seconds(7) == 7.0
    assert _retry_after_seconds(timedelta(seconds=9)) == 9.0
