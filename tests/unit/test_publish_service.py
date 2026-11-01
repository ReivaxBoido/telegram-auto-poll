"""Publishing a poll to chats (SPEC-003 FR-309, FR-310)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.polls import PollRepository
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import InstanceStatus, MessageStatus, PollStatus
from telegram_auto_poll.domain.errors import NotFoundError, ValidationError
from telegram_auto_poll.services.publish_service import PublishService
from tests.conftest import Seed
from tests.factories import FIXED_NOW
from tests.fakes import FakeClock


@dataclass
class Waker:
    calls: int = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.fixture
def waker() -> Waker:
    return Waker()


@pytest.fixture
def service(factory: async_sessionmaker[AsyncSession], waker: Waker) -> PublishService:
    return PublishService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        wake=waker,
    )


async def test_publish_creates_an_instance_and_one_message_per_chat(
    service: PublishService, seed: Seed, factory: async_sessionmaker[AsyncSession], waker: Waker
) -> None:
    result = await service.publish(
        actor_user_id=1,
        poll_id=seed.poll_id,
        chat_ids=[seed.chat_id],
    )

    assert result.instance.status is InstanceStatus.OPEN
    assert len(result.messages) == 1
    assert result.messages[0].status is MessageStatus.PENDING
    assert result.poll.status is PollStatus.ACTIVE
    assert waker.calls == 1


async def test_publish_requires_a_target(service: PublishService, seed: Seed) -> None:
    with pytest.raises(ValidationError, match="select at least one"):
        await service.publish(actor_user_id=1, poll_id=seed.poll_id, chat_ids=[])


async def test_publish_rejects_an_unknown_or_inactive_chat(
    service: PublishService, seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(ValidationError, match="not registered or is inactive"):
        await service.publish(actor_user_id=1, poll_id=seed.poll_id, chat_ids=[999])

    async with session_scope(factory) as session:
        await session.execute(
            text("UPDATE chats SET is_active = 0 WHERE id = :id"),
            {"id": seed.chat_id},
        )
    with pytest.raises(ValidationError, match="not registered or is inactive"):
        await service.publish(actor_user_id=1, poll_id=seed.poll_id, chat_ids=[seed.chat_id])


async def test_publish_rejects_unknown_or_deleted_poll(
    service: PublishService, seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(NotFoundError):
        await service.publish(actor_user_id=1, poll_id=999, chat_ids=[seed.chat_id])

    async with session_scope(factory) as session:
        await PollRepository(session).set_status(seed.poll_id, PollStatus.DELETED, now=FIXED_NOW)
    with pytest.raises(NotFoundError):
        await service.publish(actor_user_id=1, poll_id=seed.poll_id, chat_ids=[seed.chat_id])
