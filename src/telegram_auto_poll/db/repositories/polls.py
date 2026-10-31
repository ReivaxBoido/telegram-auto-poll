"""Poll repository, including the ``poll_targets`` association."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import Poll
from ..models import PollRow, PollTargetRow
from .common import dumps, from_epoch, load_str_list, to_epoch


def _to_poll(row: PollRow, targets: tuple[int, ...]) -> Poll:
    return Poll(
        question=row.question,
        options=tuple(load_str_list(row.options_json)),
        description=row.description,
        is_anonymous=bool(row.is_anonymous),
        allows_multiple_answers=bool(row.allows_multiple_answers),
        is_archived=not bool(row.is_active),
        targets=targets,
        id=row.id,
        created_at=from_epoch(row.created_at),
    )


class PollRepository:
    """Reads and writes ``polls`` and ``poll_targets``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _targets_for(self, poll_ids: Sequence[int]) -> dict[int, tuple[int, ...]]:
        if not poll_ids:
            return {}
        result = await self._session.execute(
            select(PollTargetRow.poll_id, PollTargetRow.chat_id)
            .where(PollTargetRow.poll_id.in_(poll_ids))
            .order_by(PollTargetRow.chat_id)
        )
        grouped: dict[int, list[int]] = {}
        for poll_id, chat_id in result:
            grouped.setdefault(poll_id, []).append(chat_id)
        return {poll_id: tuple(chats) for poll_id, chats in grouped.items()}

    async def add(self, poll: Poll, *, now: datetime) -> Poll:
        stamp = to_epoch(now)
        row = PollRow(
            question=poll.question,
            description=poll.description,
            options_json=dumps(list(poll.options)),
            is_anonymous=1 if poll.is_anonymous else 0,
            allows_multiple_answers=1 if poll.allows_multiple_answers else 0,
            is_active=0 if poll.is_archived else 1,
            created_at=stamp,
            updated_at=stamp,
        )
        self._session.add(row)
        await self._session.flush()
        for chat_id in poll.targets:
            self._session.add(PollTargetRow(poll_id=row.id, chat_id=chat_id, created_at=stamp))
        await self._session.flush()
        return _to_poll(row, tuple(sorted(poll.targets)))

    async def get(self, poll_id: int) -> Poll | None:
        row = await self._session.get(PollRow, poll_id)
        if row is None:
            return None
        targets = await self._targets_for([row.id])
        return _to_poll(row, targets.get(row.id, ()))

    async def list_all(
        self, *, active_only: bool = False, limit: int = 8, offset: int = 0
    ) -> list[Poll]:
        statement = select(PollRow).order_by(PollRow.id).limit(limit).offset(offset)
        if active_only:
            statement = statement.where(PollRow.is_active == 1)
        rows = list((await self._session.scalars(statement)).all())
        targets = await self._targets_for([row.id for row in rows])
        return [_to_poll(row, targets.get(row.id, ())) for row in rows]

    async def targets_for_chat(self, chat_id: int) -> dict[int, tuple[int, ...]]:
        """Return ``poll_id -> target chat ids`` for every poll that lists ``chat_id``."""
        poll_ids = select(PollTargetRow.poll_id).where(PollTargetRow.chat_id == chat_id)
        result = await self._session.execute(
            select(PollTargetRow.poll_id, PollTargetRow.chat_id)
            .where(PollTargetRow.poll_id.in_(poll_ids))
            .order_by(PollTargetRow.poll_id, PollTargetRow.chat_id)
        )
        grouped: dict[int, list[int]] = {}
        for poll_id, target_id in result:
            grouped.setdefault(poll_id, []).append(target_id)
        return {poll_id: tuple(targets) for poll_id, targets in grouped.items()}

    async def set_targets(self, poll_id: int, chat_ids: Sequence[int], *, now: datetime) -> None:
        stamp = to_epoch(now)
        await self._session.execute(delete(PollTargetRow).where(PollTargetRow.poll_id == poll_id))
        for chat_id in sorted(set(chat_ids)):
            self._session.add(PollTargetRow(poll_id=poll_id, chat_id=chat_id, created_at=stamp))
        await self._session.flush()

    async def set_active(self, poll_id: int, *, active: bool, now: datetime) -> Poll | None:
        row = await self._session.get(PollRow, poll_id)
        if row is None:
            return None
        row.is_active = 1 if active else 0
        row.updated_at = to_epoch(now)
        await self._session.flush()
        targets = await self._targets_for([row.id])
        return _to_poll(row, targets.get(row.id, ()))

    async def count(self, *, active_only: bool = False) -> int:
        statement = select(func.count()).select_from(PollRow)
        if active_only:
            statement = statement.where(PollRow.is_active == 1)
        result = await self._session.scalar(statement)
        return int(result or 0)
