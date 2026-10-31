"""Telegram poll sender (FR-129, FR-131).

The adapter is the only place where PTB exceptions meet the domain: it classifies each failure as
retryable or terminal and never lets a library type escape into a service. No ``parse_mode`` is
passed, so a question containing ``<b>`` is delivered literally (FR-120).
"""

from __future__ import annotations

from datetime import timedelta

from telegram import Bot
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

from ..domain.entities import PollRequest
from ..domain.errors import DeliveryError


def _retry_after_seconds(value: int | timedelta) -> float:
    if isinstance(value, timedelta):
        return value.total_seconds()
    return float(value)


class TelegramPollSender:
    """Sends a regular poll with the Bot API and returns its message id."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_poll(self, request: PollRequest) -> int:
        """Send one poll.

        Raises:
            DeliveryError: any Telegram failure, classified for the retry policy.
        """
        payload: dict[str, object] = {
            "chat_id": request.chat_id,
            "question": request.question,
            "options": list(request.options),
            "is_anonymous": request.is_anonymous,
            "allows_multiple_answers": request.allows_multiple_answers,
            "type": "regular",
        }
        if request.description is not None:
            payload["description"] = request.description

        try:
            message = await self._bot.send_poll(**payload)  # type: ignore[arg-type]
        except RetryAfter as exc:
            raise DeliveryError(
                f"rate limited: retry after {_retry_after_seconds(exc.retry_after)}s",
                retryable=True,
                retry_after=_retry_after_seconds(exc.retry_after),
                classification="retry_after",
            ) from exc
        # ``BadRequest`` derives from ``NetworkError`` in python-telegram-bot, so the terminal
        # classifications must be caught before the retryable network family.
        except BadRequest as exc:
            raise DeliveryError(str(exc), retryable=False, classification="bad_request") from exc
        except Forbidden as exc:
            raise DeliveryError(str(exc), retryable=False, classification="forbidden") from exc
        except TimedOut as exc:
            raise DeliveryError(str(exc), retryable=True, classification="timeout") from exc
        except NetworkError as exc:
            raise DeliveryError(str(exc), retryable=True, classification="network") from exc
        except TelegramError as exc:
            raise DeliveryError(str(exc), retryable=False, classification="telegram") from exc

        return int(message.message_id)
