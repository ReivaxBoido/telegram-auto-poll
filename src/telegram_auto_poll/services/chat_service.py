"""Chat registration, listing, and deactivation (SPEC-003 FR-112 - FR-115)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..db.uow import UnitOfWork
from ..domain.entities import AuditEvent, Chat
from ..domain.enums import AuditAction, ChatType, SkipReason
from ..domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from ..domain.ports import ChatGateway, Clock
from ..domain.validation import validate_chat_type
from .pagination import PAGE_SIZE, Page, offset_for

#: Group, supergroup, and channel ids are negative and have at least five digits. A positive value
#: is a user id, which is never a valid target.
_CHAT_ID = re.compile(r"^-\d{5,}$")

_GROUP_WARNING = "the bot is not an administrator in this group; it may not be able to post polls"
_CHANNEL_PERMISSION = (
    "the bot must be an administrator in this channel with the 'Post messages' permission"
)


def parse_chat_id(text: str) -> int:
    """Parse a pasted chat id, rejecting users, bots, and non-numeric input."""
    candidate = text.strip()
    if not _CHAT_ID.fullmatch(candidate):
        raise ValidationError(
            "chat_id",
            "paste the numeric chat id; it is negative for groups, supergroups, and channels",
        )
    return int(candidate)


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """The stored chat plus what the admin should be told about it."""

    chat: Chat
    reactivated: bool
    warning: str | None


@dataclass(frozen=True, slots=True)
class DeactivationResult:
    """What deactivating a chat changed."""

    chat: Chat
    skipped_messages: int


class ChatService:
    """Registers, lists, and deactivates target chats."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        gateway: ChatGateway,
        clock: Clock,
        bot_user_id: int,
        page_size: int = PAGE_SIZE,
    ) -> None:
        self._uow = uow
        self._gateway = gateway
        self._clock = clock
        self._bot_user_id = bot_user_id
        self._page_size = page_size

    async def register(self, telegram_chat_id: int, *, actor_user_id: int) -> RegistrationResult:
        """Resolve the chat on Telegram and store or reactivate it.

        Raises:
            NotFoundError: Telegram does not expose the chat to the bot.
            PermissionDeniedError: a channel without the bot's posting rights.
            ValidationError: a private chat or an unsupported chat type.
        """
        try:
            info = await self._gateway.get_chat(telegram_chat_id)
        except LookupError as exc:
            raise NotFoundError(
                f"Telegram does not expose chat {telegram_chat_id}; add the bot to it first"
            ) from exc

        chat_type = validate_chat_type(str(info.type))
        permitted = await self._gateway.get_member(telegram_chat_id, self._bot_user_id)
        if chat_type is ChatType.CHANNEL and not permitted:
            raise PermissionDeniedError(_CHANNEL_PERMISSION)
        warning = None if permitted else _GROUP_WARNING

        now = self._clock.now()
        async with self._uow() as repos:
            existing = await repos.chats.get_by_telegram_id(telegram_chat_id)
            candidate = Chat.create(
                telegram_chat_id=telegram_chat_id,
                chat_type=chat_type.value,
                title=info.title,
                now=now,
            )
            stored = await repos.chats.upsert(candidate, now=now)
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.CHAT_REGISTER.value,
                    entity_type="chat",
                    entity_id=stored.id or 0,
                    details={
                        "telegram_chat_id": telegram_chat_id,
                        "type": chat_type.value,
                    },
                    created_at=now,
                ),
                now=now,
            )

        reactivated = existing is not None and not existing.is_active
        return RegistrationResult(chat=stored, reactivated=reactivated, warning=warning)

    async def deactivate(self, chat_id: int, *, actor_user_id: int) -> DeactivationResult:
        """Deactivate a chat and skip its pending messages.

        Raises:
            NotFoundError: no chat has that surrogate id.
        """
        now = self._clock.now()
        async with self._uow() as repos:
            chat = await repos.chats.get(chat_id)
            if chat is None:
                raise NotFoundError(f"chat {chat_id} is not registered")
            if not chat.is_active:
                return DeactivationResult(chat=chat, skipped_messages=0)

            await repos.chats.set_active(chat_id, active=False, now=now)
            deactivated = replace(chat, is_active=False)
            skipped = await repos.messages.skip_pending_for_chat(
                chat_id, reason=SkipReason.CHAT_INACTIVE.value, now=now
            )
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.CHAT_DEACTIVATE.value,
                    entity_type="chat",
                    entity_id=chat_id,
                    details={"skipped_messages": skipped},
                    created_at=now,
                ),
                now=now,
            )
            return DeactivationResult(chat=deactivated, skipped_messages=skipped)

    async def page(self, number: int, *, active_only: bool = False) -> Page[Chat]:
        """Return one page of registered chats, ordered by Telegram chat id."""
        offset = offset_for(number, self._page_size)
        async with self._uow() as repos:
            total = await repos.chats.count(active_only=active_only)
            items = await repos.chats.list_all(
                active_only=active_only, limit=self._page_size, offset=offset
            )
        return Page.create(items, number=number, size=self._page_size, total=total)

    async def page_sized(
        self,
        number: int,
        *,
        size: int,
        active_only: bool = True,
    ) -> Page[Chat]:
        """Return one page with an explicit size (the publish selector uses six rows)."""
        offset = offset_for(number, size)
        async with self._uow() as repos:
            total = await repos.chats.count(active_only=active_only)
            items = await repos.chats.list_all(active_only=active_only, limit=size, offset=offset)
        return Page.create(items, number=number, size=size, total=total)
