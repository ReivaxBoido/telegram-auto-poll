"""Telegram chat gateway and forwarded-message extraction (FR-112, FR-113).

``get_member`` answers the only question registration asks: may the bot post here? Channels need
``can_post_messages``; groups only need the bot to be an administrator (restricted members with
``can_send_messages`` also qualify).
"""

from __future__ import annotations

from telegram import Bot
from telegram.error import TelegramError

from ..domain.entities import ChatInfo
from ..domain.enums import ChatType
from ..domain.errors import NotFoundError, PermissionDeniedError, ValidationError

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


class TelegramChatGateway:
    """Reads chat metadata and bot rights from Telegram."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def get_chat(self, chat_id: int) -> ChatInfo:
        """Return the chat's type and title.

        Raises:
            NotFoundError: Telegram does not expose the chat to the bot.
            ValidationError: a private chat or bot, which can never be a target.
        """
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
        """Return whether ``user_id`` may post in ``chat_id``.

        Raises:
            PermissionDeniedError: Telegram refuses to report the membership.
        """
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
