"""Participant vote toggling (SPEC-003 FR-318 - FR-325)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.poll_instances import PollInstanceRepository
from telegram_auto_poll.db.repositories.poll_responses import PollResponseRepository
from telegram_auto_poll.db.repositories.polls import PollRepository
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.entities import BroadcastResult
from telegram_auto_poll.domain.enums import InstanceStatus, PollStatus, VoteAction
from telegram_auto_poll.domain.errors import PollClosedError, ResponseRejectedError
from telegram_auto_poll.services.response_service import ResponseService
from tests.conftest import Seed
from tests.factories import FIXED_NOW
from tests.fakes import FakeClock


@dataclass
class Broadcaster:
    dirty: list[int] = field(default_factory=list)

    def mark_dirty(self, instance_id: int) -> None:
        self.dirty.append(instance_id)

    async def refresh(self, instance_id: int) -> BroadcastResult:
        return BroadcastResult()

    async def redact(self, instance_ids: list[int]) -> BroadcastResult:
        return BroadcastResult()


@pytest.fixture
def broadcaster() -> Broadcaster:
    return Broadcaster()


@pytest.fixture
def service(factory: async_sessionmaker[AsyncSession], broadcaster: Broadcaster) -> ResponseService:
    return ResponseService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        broadcaster=broadcaster,  # type: ignore[arg-type]
    )


async def test_toggle_adds_then_removes_and_marks_dirty(
    service: ResponseService,
    seed: Seed,
    broadcaster: Broadcaster,
) -> None:
    added = await service.toggle(
        instance_id=seed.instance_id,
        option_id=seed.poll.options[0].id or 0,
        telegram_user_id=42,
        first_name="Alice",
        chat_id=seed.chat.telegram_chat_id,
        message_id=seed.message.telegram_message_id,
    )
    removed = await service.toggle(
        instance_id=seed.instance_id,
        option_id=seed.poll.options[0].id or 0,
        telegram_user_id=42,
        first_name="Alice",
        chat_id=seed.chat.telegram_chat_id,
        message_id=seed.message.telegram_message_id,
    )

    assert added.action is VoteAction.ADDED
    assert removed.action is VoteAction.REMOVED
    assert broadcaster.dirty == [seed.instance_id, seed.instance_id]


async def test_multi_select_stores_one_row_per_option(
    service: ResponseService,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    for option in seed.poll.options:
        await service.toggle(
            instance_id=seed.instance_id,
            option_id=option.id or 0,
            telegram_user_id=42,
            first_name="Alice",
            chat_id=seed.chat.telegram_chat_id,
            message_id=seed.message.telegram_message_id,
        )

    async with session_scope(factory) as session:
        rows = await PollResponseRepository(session).list_for_instance(seed.instance_id)
    assert len(rows) == 2


async def test_closed_instance_rejects(
    service: ResponseService,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await PollInstanceRepository(session).set_status(
            seed.instance_id, InstanceStatus.CLOSED, now=FIXED_NOW, closed_at=FIXED_NOW
        )
    with pytest.raises(PollClosedError, match="Voting is closed"):
        await service.toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            chat_id=seed.chat.telegram_chat_id,
            message_id=seed.message.telegram_message_id,
        )


async def test_deleted_poll_and_unknown_option_reject(
    service: ResponseService,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(ResponseRejectedError, match="option no longer exists"):
        await service.toggle(
            instance_id=seed.instance_id,
            option_id=999,
            telegram_user_id=1,
            first_name="Alice",
            chat_id=seed.chat.telegram_chat_id,
            message_id=seed.message.telegram_message_id,
        )

    async with session_scope(factory) as session:
        await PollRepository(session).set_status(seed.poll_id, PollStatus.DELETED, now=FIXED_NOW)
    with pytest.raises(ResponseRejectedError, match="no longer available"):
        await service.toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            chat_id=seed.chat.telegram_chat_id,
            message_id=seed.message.telegram_message_id,
        )


async def test_wrong_chat_or_message_rejects(service: ResponseService, seed: Seed) -> None:
    with pytest.raises(ResponseRejectedError, match="message is no longer active"):
        await service.toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            chat_id=-999,
            message_id=seed.message.telegram_message_id,
        )
    with pytest.raises(ResponseRejectedError, match="message is no longer active"):
        await service.toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            chat_id=seed.chat.telegram_chat_id,
            message_id=999,
        )


async def test_inline_message_must_match_a_stored_row(service: ResponseService, seed: Seed) -> None:
    with pytest.raises(ResponseRejectedError):
        await service.toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            inline_message_id="inline-1",
        )
