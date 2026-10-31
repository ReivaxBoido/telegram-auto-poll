"""Chat repository: statement builders only, no commits and no business policy."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import Chat
from ..models import ChatRow
from .common import chat_type, from_epoch, to_epoch


def _to_chat(row: ChatRow) -> Chat:
    return Chat(
        telegram_chat_id=row.telegram_chat_id,
        type=chat_type(row.type),
        title=row.title,
        is_active=bool(row.is_active),
        id=row.id,
        created_at=from_epoch(row.created_at),
    )


class ChatRepository:
    """Reads and writes ``chats``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_chat_id: int) -> Chat | None:
        row = await self._session.scalar(
            select(ChatRow).where(ChatRow.telegram_chat_id == telegram_chat_id)
        )
        return None if row is None else _to_chat(row)

    async def get(self, chat_id: int) -> Chat | None:
        row = await self._session.get(ChatRow, chat_id)
        return None if row is None else _to_chat(row)

    async def upsert(self, chat: Chat, *, now: datetime) -> Chat:
        """Insert or reactivate a chat, refreshing its Telegram metadata."""
        row = await self._session.scalar(
            select(ChatRow).where(ChatRow.telegram_chat_id == chat.telegram_chat_id)
        )
        stamp = to_epoch(now)
        if row is None:
            row = ChatRow(
                telegram_chat_id=chat.telegram_chat_id,
                type=chat.type.value,
                title=chat.title,
                is_active=1 if chat.is_active else 0,
                created_at=stamp,
                updated_at=stamp,
            )
            self._session.add(row)
        else:
            row.type = chat.type.value
            row.title = chat.title
            row.is_active = 1 if chat.is_active else 0
            row.updated_at = stamp
        await self._session.flush()
        return _to_chat(row)

    async def list_all(
        self, *, active_only: bool = False, limit: int = 8, offset: int = 0
    ) -> list[Chat]:
        statement = select(ChatRow).order_by(ChatRow.telegram_chat_id).limit(limit).offset(offset)
        if active_only:
            statement = statement.where(ChatRow.is_active == 1)
        rows = (await self._session.scalars(statement)).all()
        return [_to_chat(row) for row in rows]

    async def set_active(self, chat_id: int, *, active: bool, now: datetime) -> Chat | None:
        row = await self._session.get(ChatRow, chat_id)
        if row is None:
            return None
        row.is_active = 1 if active else 0
        row.updated_at = to_epoch(now)
        await self._session.flush()
        return _to_chat(row)

    async def active_ids(self) -> set[int]:
        """Return the surrogate ids of every active chat, for target filtering."""
        statement = select(ChatRow.id).where(ChatRow.is_active == 1)
        return set((await self._session.scalars(statement)).all())

    async def count(self, *, active_only: bool = False) -> int:
        statement = select(func.count()).select_from(ChatRow)
        if active_only:
            statement = statement.where(ChatRow.is_active == 1)
        result = await self._session.scalar(statement)
        return int(result or 0)
