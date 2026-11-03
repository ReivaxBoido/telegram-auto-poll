"""Repository behaviour against a real SQLite schema (SPEC-003 §7)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.chats import ChatRepository
from telegram_auto_poll.db.repositories.poll_instances import PollInstanceRepository
from telegram_auto_poll.db.repositories.poll_messages import PollMessageRepository
from telegram_auto_poll.db.repositories.poll_responses import PollResponseRepository
from telegram_auto_poll.db.repositories.polls import PollRepository
from telegram_auto_poll.domain.enums import InstanceStatus, MessageStatus, PollStatus
from tests.conftest import Seed
from tests.factories import FIXED_NOW, make_poll


async def test_poll_repository_crud(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        repo = PollRepository(session)
        created = await repo.add(make_poll(), now=FIXED_NOW)
        assert created.id is not None
        assert await repo.count() == 1
        assert await repo.get(created.id) is not None
        assert await repo.get(999) is None
        assert len(await repo.list_all(limit=8, offset=0)) == 1
        await repo.set_status(created.id, PollStatus.ACTIVE, now=FIXED_NOW)
        assert (await repo.get(created.id)).status is PollStatus.ACTIVE  # type: ignore[union-attr]
        assert await repo._options_for([]) == {}


async def test_instance_repository(factory: async_sessionmaker[AsyncSession], seed: Seed) -> None:
    async with session_scope(factory) as session:
        repo = PollInstanceRepository(session)
        assert await repo.get(seed.instance_id) is not None
        assert await repo.get(999) is None
        assert (await repo.latest_for_poll(seed.poll_id)).id == seed.instance_id  # type: ignore[union-attr]
        assert (
            await repo.latest_with_status(seed.poll_id, InstanceStatus.OPEN)
        ).id == seed.instance_id  # type: ignore[union-attr]
        assert await repo.latest_with_status(seed.poll_id, InstanceStatus.CLOSED) is None
        assert len(await repo.list_for_poll(seed.poll_id)) == 1
        assert await repo.count_open() == 1
        assert await repo.list_expired_with_responses(FIXED_NOW, limit=10) == []


async def test_message_repository_claim_and_statuses(
    factory: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    async with session_scope(factory) as session:
        repo = PollMessageRepository(session)
        await repo.reset_for_retry(seed.message_id, next_attempt_at=FIXED_NOW, now=FIXED_NOW)
        claimed = await repo.claim_pending(now=FIXED_NOW, limit=10)
        assert len(claimed) == 1
        assert claimed[0].status is MessageStatus.SENDING
        await repo.mark_sent(seed.message_id, 777, now=FIXED_NOW)
        assert (await repo.get(seed.message_id)).status is MessageStatus.SENT  # type: ignore[union-attr]
        await repo.mark_failed(seed.message_id, "bad", now=FIXED_NOW)
        await repo.mark_skipped(seed.message_id, "inactive", now=FIXED_NOW)
        await repo.mark_missing(seed.message_id, now=FIXED_NOW)
        await repo.reset_for_retry(seed.message_id, next_attempt_at=FIXED_NOW, now=FIXED_NOW)
        await repo.skip_pending_for_instance(seed.instance_id, "closed", now=FIXED_NOW)
        await repo.reset_for_retry(seed.message_id, next_attempt_at=FIXED_NOW, now=FIXED_NOW)
        await repo.skip_pending_for_chat(seed.chat_id, "chat_inactive", now=FIXED_NOW)
        assert await repo.counts()
        assert await repo.count_pending() == 0
        assert await repo.list_recent_failures(limit=5) == []
        assert (
            await repo.get_for_chat_message(
                instance_id=seed.instance_id,
                chat_id=seed.chat_id,
                telegram_message_id=777,
            )
            is not None
        )
        assert (
            await repo.get_for_inline(instance_id=seed.instance_id, inline_message_id="x") is None
        )
        assert await repo.delete_batch_before(FIXED_NOW - timedelta(days=1), limit=10) == 0
        assert await repo.delete_batch_before(FIXED_NOW + timedelta(days=1), limit=10) == 1
        assert await repo.fail_stale_sending("ambiguous", now=FIXED_NOW) == 0


async def test_response_repository_toggle_and_delete(
    factory: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    async with session_scope(factory) as session:
        repo = PollResponseRepository(session)
        added = await repo.toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            now=FIXED_NOW,
        )
        assert added is True
        assert await repo.count_for_instance(seed.instance_id) == 1
        assert await repo.counts_by_option(seed.instance_id) == {seed.poll.options[0].id or 0: 1}
        assert len(await repo.list_for_instance(seed.instance_id)) == 1
        removed = await repo.toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            now=FIXED_NOW,
        )
        assert removed is False
        assert await repo.delete_for_instances([]) == 0
        assert await repo.delete_for_instance(seed.instance_id) == 0
        assert await repo.count_all() == 0


async def test_chat_set_active_unknown_returns_none(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        assert await ChatRepository(session).set_active(999, active=False, now=FIXED_NOW) is None
