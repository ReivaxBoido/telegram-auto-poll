"""Telegram adapters: chat metadata and bot-authored message send/edit.

The adapter is the only place where PTB exceptions meet the domain: it classifies each failure as
retryable or terminal and never lets a library type escape into a service. No ``parse_mode`` is
passed, so user text is delivered literally.
"""

from __future__ import annotations

from datetime import timedelta

from telegram import Bot, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

from ..domain.entities import ChatInfo, Keyboard
from ..domain.enums import ChatType
from ..domain.errors import DeliveryError, NotFoundError, PermissionDeniedError, ValidationError
from .keyboards import build_keyboard

_ADMIN_STATUSES = frozenset({"administrator", "creator"})


def forwarded_chat_id(message: object) -> int | None:
    """Return the origin chat id of a forwarded message, old or new Bot API shape."""
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        sender_chat = getattr(origin, "sender_chat", None)
        if sender_chat is not None:
            return int(sender_chat.id)
        chat = getattr(origin, "chat", None)
        if chat is not None:
            return int(chat.id)
    legacy = getattr(message, "forward_from_chat", None)
    if legacy is not None:
        return int(legacy.id)
    return None


def _retry_after_seconds(value: int | timedelta) -> float:
    if isinstance(value, timedelta):
        return value.total_seconds()
    return float(value)


def _keyboard_markup(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    return None if keyboard is None else build_keyboard(keyboard)


def _classify_edit_error(exc: TelegramError) -> DeliveryError:
    if isinstance(exc, RetryAfter):
        return DeliveryError(
            f"rate limited: retry after {_retry_after_seconds(exc.retry_after)}s",
            retryable=True,
            retry_after=_retry_after_seconds(exc.retry_after),
            classification="retry_after",
        )
    if isinstance(exc, BadRequest):
        message = str(exc).lower()
        if "message is not modified" in message:
            return DeliveryError(
                "message is not modified", retryable=False, classification="not_modified"
            )
        if "message to edit not found" in message or "message can't be edited" in message:
            return DeliveryError(
                "message not found",
                retryable=False,
                classification="message_not_found",
            )
        return DeliveryError(str(exc), retryable=False, classification="bad_request")
    if isinstance(exc, Forbidden):
        return DeliveryError(str(exc), retryable=False, classification="forbidden")
    if isinstance(exc, TimedOut):
        return DeliveryError(str(exc), retryable=True, classification="timeout")
    if isinstance(exc, NetworkError):
        return DeliveryError(str(exc), retryable=True, classification="network")
    return DeliveryError(str(exc), retryable=False, classification="telegram")


class TelegramChatGateway:
    """Reads chat metadata and bot rights from Telegram."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def get_chat(self, chat_id: int) -> ChatInfo:
        """Return the chat's type and title."""
        try:
            chat = await self._bot.get_chat(chat_id)
        except TelegramError as exc:
            raise NotFoundError(f"Telegram does not expose chat {chat_id}") from exc

        try:
            chat_type = ChatType(str(chat.type))
        except ValueError as exc:
            raise ValidationError(
                "chat_type", "only groups, supergroups, and channels can be registered"
            ) from exc
        title = getattr(chat, "title", None) or ""
        return ChatInfo(chat_id=int(chat.id), type=chat_type, title=str(title))

    async def get_member(self, chat_id: int, user_id: int) -> bool:
        """Return whether ``user_id`` may post in ``chat_id``."""
        try:
            member = await self._bot.get_chat_member(chat_id, user_id)
        except TelegramError as exc:
            raise PermissionDeniedError(
                f"cannot verify the bot's rights in chat {chat_id}"
            ) from exc

        status = str(member.status)
        if status in _ADMIN_STATUSES:
            return bool(getattr(member, "can_post_messages", True))
        if status == "restricted":
            return bool(getattr(member, "can_send_messages", False))
        return False


class TelegramMessageGateway:
    """Sends and edits messages through the Bot API."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Keyboard | None = None,
    ) -> int:
        """Send one message and return its Telegram message id."""
        try:
            message = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=_keyboard_markup(keyboard),
            )
        except RetryAfter as exc:
            raise DeliveryError(
                f"rate limited: retry after {_retry_after_seconds(exc.retry_after)}s",
                retryable=True,
                retry_after=_retry_after_seconds(exc.retry_after),
                classification="retry_after",
            ) from exc
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

    async def edit_message(
        self,
        *,
        text: str,
        chat_id: int | None = None,
        message_id: int | None = None,
        inline_message_id: str | None = None,
        keyboard: Keyboard | None = None,
    ) -> None:
        """Edit an existing message; ``not modified`` is treated as success."""
        payload: dict[str, object] = {
            "text": text,
            "reply_markup": _keyboard_markup(keyboard),
        }
        if inline_message_id is not None:
            payload["inline_message_id"] = inline_message_id
        else:
            payload["chat_id"] = chat_id
            payload["message_id"] = message_id
        try:
            await self._bot.edit_message_text(**payload)  # type: ignore[arg-type]
        except TelegramError as exc:
            error = _classify_edit_error(exc)
            if error.classification == "not_modified":
                return
            raise error from exc
