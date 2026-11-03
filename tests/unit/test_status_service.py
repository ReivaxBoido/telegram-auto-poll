"""Status counters (SPEC-003 FR-338)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.poll_responses import PollResponseRepository
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.services.status_service import StatusService
from tests.conftest import Seed
from tests.factories import FIXED_NOW


async def test_snapshot_counts_without_names(
    factory: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    async with session_scope(factory) as session:
        await PollResponseRepository(session).toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            now=FIXED_NOW,
        )

    snapshot = await StatusService(uow=lambda: unit_of_work(factory)).snapshot()

    assert snapshot.active_chats == 1
    assert snapshot.polls == 1
    assert snapshot.open_instances == 1
    assert snapshot.pending_messages == 0
    assert snapshot.active_responses == 1
    assert snapshot.recent_failures == ()
