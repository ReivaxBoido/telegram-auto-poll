"""Audit service (SPEC-003 FR-336)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.services.audit_service import AuditService
from tests.factories import FIXED_NOW


async def test_record_recent_and_history(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = AuditService(uow=lambda: unit_of_work(factory))

    async with unit_of_work(factory) as repos:
        await service.record(
            repos,
            actor_user_id=1,
            action="poll.create",  # type: ignore[arg-type]
            entity_type="poll",
            entity_id=10,
            now=FIXED_NOW,
            details={"title": "Game"},
        )

    recent = await service.recent()
    history = await service.history("poll", 10)

    assert len(recent) == 1
    assert recent[0].action == "poll.create"
    assert history[0].entity_id == 10
