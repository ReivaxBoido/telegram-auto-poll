"""Retention (SPEC-003 FR-337)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.audit import AuditRepository
from telegram_auto_poll.db.repositories.poll_instances import PollInstanceRepository
from telegram_auto_poll.db.repositories.poll_responses import PollResponseRepository
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.entities import AuditEvent, BroadcastResult
from telegram_auto_poll.domain.enums import InstanceStatus
from telegram_auto_poll.services.retention_service import RetentionService
from tests.conftest import Seed
from tests.factories import FIXED_NOW
from tests.fakes import FakeClock


@dataclass
class Redactor:
    instances: list[int] = field(default_factory=list)

    async def __call__(self, instance_ids: list[int]) -> BroadcastResult:
        self.instances.extend(instance_ids)
        return BroadcastResult(edited=len(instance_ids))


@dataclass
class Checkpoint:
    calls: int = 0

    async def __call__(self) -> None:
        self.calls += 1


@pytest.fixture
def redactor() -> Redactor:
    return Redactor()


@pytest.fixture
def checkpoint() -> Checkpoint:
    return Checkpoint()


@pytest.fixture
def service(
    factory: async_sessionmaker[AsyncSession],
    redactor: Redactor,
    checkpoint: Checkpoint,
) -> RetentionService:
    return RetentionService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        checkpoint=checkpoint,
        redact=redactor,
        response_retention_days=30,
        retention_days=30,
        batch_size=2,
    )


async def test_open_instance_responses_are_retained(
    service: RetentionService, seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        await PollResponseRepository(session).toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            now=FIXED_NOW,
        )

    result = await service.run()

    assert result.responses == 0


async def test_closed_instance_is_redacted_then_pruned_idempotently(
    service: RetentionService,
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    redactor: Redactor,
    checkpoint: Checkpoint,
) -> None:
    old = FIXED_NOW - timedelta(days=100)
    async with session_scope(factory) as session:
        await PollResponseRepository(session).toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            now=old,
        )
        await PollInstanceRepository(session).set_status(
            seed.instance_id, InstanceStatus.CLOSED, now=old, closed_at=old
        )

    first = await service.run()
    second = await service.run()

    assert first.responses == 1
    assert second.responses == 0
    assert redactor.instances == [seed.instance_id]
    assert checkpoint.calls == 2


async def test_old_messages_and_audit_are_pruned(
    service: RetentionService, seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    old = FIXED_NOW - timedelta(days=100)
    async with session_scope(factory) as session:
        await AuditRepository(session).add(
            AuditEvent(
                actor_user_id=1,
                action="poll.create",
                entity_type="poll",
                entity_id=seed.poll_id,
                created_at=old,
            ),
            now=old,
        )
        await session.execute(
            text("UPDATE poll_messages SET created_at = :old WHERE id = :id"),
            {"old": int(old.timestamp()), "id": seed.message_id},
        )

    result = await service.run()

    assert result.messages == 1
    assert result.audit_events == 1
