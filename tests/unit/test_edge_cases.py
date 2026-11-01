"""Targeted edge cases for branches that are hard to reach via the happy path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.bot.auth import AdminGuard, AuthDenial
from telegram_auto_poll.bot.publish_store import PublishSession, PublishStore
from telegram_auto_poll.bot.wizards.engine import advance
from telegram_auto_poll.bot.wizards.states import WizardEvent, WizardInput, WizardState, WizardStep
from telegram_auto_poll.bot.wizards.store import WizardStore
from telegram_auto_poll.config import Settings
from telegram_auto_poll.db.engine import check_database, session_scope
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.callbacks import CallbackDataError, decode
from telegram_auto_poll.domain.entities import BroadcastResult
from telegram_auto_poll.domain.enums import PollStatus
from telegram_auto_poll.domain.errors import NotFoundError, ResponseRejectedError
from telegram_auto_poll.domain.poll_render import render_poll_message
from telegram_auto_poll.services.poll_service import PollService
from telegram_auto_poll.services.recovery_service import RecoveryService
from telegram_auto_poll.services.response_service import ResponseService
from telegram_auto_poll.services.retention_service import RetentionService
from tests.conftest import ADMIN_USER_ID, Seed
from tests.factories import FIXED_NOW, make_instance, make_poll
from tests.fakes import FakeClock, FakeNotifier


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


def test_decode_rejects_invalid_action_and_argument() -> None:
    with pytest.raises(CallbackDataError, match="invalid callback action"):
        decode("v1:v:Bad:1")
    with pytest.raises(CallbackDataError, match="invalid callback argument"):
        decode("v1:v:toggle:bad:value")


def test_render_rejects_a_message_that_cannot_fit() -> None:
    with pytest.raises(ValueError, match="exceeds 10"):
        render_poll_message(
            make_poll(status=PollStatus.ACTIVE),
            make_instance(),
            (),
            names_per_option=10,
            max_length=10,
        )


def test_wizard_back_without_history_and_invalid_inputs() -> None:
    state = WizardState(admin_user_id=1, step=WizardStep.TITLE)
    assert advance(state, WizardInput(WizardEvent.BACK)).state.step is WizardStep.TITLE

    options = WizardState(admin_user_id=1, step=WizardStep.OPTIONS, title="Game")
    non_text = advance(options, WizardInput(WizardEvent.CONFIRM))
    assert "not understood" in (non_text.state.error or "")
    invalid = advance(options, WizardInput(WizardEvent.TEXT, text=""))
    assert "option must be 1-100" in (invalid.state.error or "")

    preview = WizardState(admin_user_id=1, step=WizardStep.PREVIEW, title="Game", options=("In",))
    non_confirm = advance(preview, WizardInput(WizardEvent.TEXT, text="x"))
    assert "press Confirm" in (non_confirm.state.error or "")


def test_auth_callback_without_data_or_user() -> None:
    guard = AdminGuard(admin_user_ids=frozenset({ADMIN_USER_ID}), clock=FakeClock())
    no_data = SimpleNamespace(
        callback_query=SimpleNamespace(
            from_user=SimpleNamespace(id=ADMIN_USER_ID),
            data=None,
            message=SimpleNamespace(chat=SimpleNamespace(type="private")),
        )
    )
    assert guard.authorize_admin_callback(no_data).reason is AuthDenial.BAD_CALLBACK

    no_user = SimpleNamespace(
        callback_query=SimpleNamespace(
            from_user=None,
            data="v1:v:toggle:1_1",
            message=None,
        )
    )
    assert guard.authorize_vote_callback(no_user).reason is AuthDenial.BAD_CALLBACK


def test_command_budget_prunes_old_entries() -> None:
    clock = FakeClock()
    guard = AdminGuard(
        admin_user_ids=frozenset({ADMIN_USER_ID}), clock=clock, commands_per_minute=1
    )
    assert guard.consume_command(ADMIN_USER_ID, "help").allowed is True
    clock.advance(61)
    assert guard.consume_command(ADMIN_USER_ID, "help").allowed is True


def test_config_validators_directly() -> None:
    assert Settings._split_admin_ids([1, 2]) == [1, 2]
    with pytest.raises(ValueError, match="at least one"):
        Settings._require_positive_admin_ids(frozenset())
    with pytest.raises(ValueError, match="positive"):
        Settings._require_positive_admin_ids(frozenset({0}))


async def test_check_database_returns_false_on_failure() -> None:
    class BrokenFactory:
        def __call__(self) -> object:
            raise OSError("boom")

    assert await check_database(BrokenFactory()) is False  # type: ignore[arg-type]


async def test_wizard_and_publish_stores_expire() -> None:
    clock = FakeClock()
    wizards = WizardStore(clock=clock, ttl_seconds=10)
    wizards.put(WizardState(admin_user_id=1))
    clock.advance(11)
    assert wizards.expire() == 1
    assert wizards.active_count() == 0

    publishes = PublishStore(clock=clock, ttl_seconds=10)
    publishes.put(PublishSession(admin_user_id=1, poll_id=1))
    clock.advance(11)
    assert publishes.expire() == 1
    assert publishes.active_count() == 0
    publishes.put(PublishSession(admin_user_id=2, poll_id=1))
    publishes.discard(2)
    assert publishes.active_count() == 0


async def test_poll_service_refresh_without_instance_and_page_count(
    factory: async_sessionmaker[AsyncSession],
    broadcaster: Broadcaster,
) -> None:
    service = PollService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        broadcaster=broadcaster,  # type: ignore[arg-type]
    )
    created = await service.create(actor_user_id=1, title="Draft", options=["In"])
    page = await service.page(1)
    assert page.items[0].response_count == 0
    with pytest.raises(NotFoundError, match="has not been published"):
        await service.refresh(created.id or 0, actor_user_id=1)


async def test_response_service_requires_a_message_reference(
    factory: async_sessionmaker[AsyncSession], seed: Seed, broadcaster: Broadcaster
) -> None:
    service = ResponseService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        broadcaster=broadcaster,  # type: ignore[arg-type]
    )
    with pytest.raises(ResponseRejectedError, match="no longer active"):
        await service.toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
        )


async def test_recovery_with_no_stale_rows(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    notifier = FakeNotifier()
    result = await RecoveryService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        notifier=notifier,
    ).run()
    assert result.failed_messages == 0
    assert notifier.messages == []


async def test_retention_with_empty_database(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async def redact(instance_ids: list[int]) -> BroadcastResult:
        return BroadcastResult()

    async def checkpoint() -> None:
        return None

    result = await RetentionService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        checkpoint=checkpoint,
        redact=redact,
        batch_size=1,
    ).run()
    assert result.responses == 0
    assert result.messages == 0
    assert result.audit_events == 0


async def test_retention_loops_over_full_batches(
    factory: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    async def redact(instance_ids: list[int]) -> BroadcastResult:
        return BroadcastResult()

    async def checkpoint() -> None:
        return None

    old = int((FIXED_NOW - timedelta(days=100)).timestamp())
    async with session_scope(factory) as session:
        await session.execute(
            text("UPDATE poll_messages SET created_at = :old WHERE id = :id"),
            {"old": old, "id": seed.message_id},
        )
        await session.execute(
            text(
                "INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, "
                "details_json, created_at) VALUES (1, 'poll.create', 'poll', 1, NULL, :old)"
            ),
            {"old": old},
        )

    result = await RetentionService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        checkpoint=checkpoint,
        redact=redact,
        retention_days=30,
        batch_size=1,
    ).run()
    assert result.messages == 1
    assert result.audit_events == 1


async def test_store_get_removes_expired_sessions() -> None:
    clock = FakeClock()
    wizards = WizardStore(clock=clock, ttl_seconds=10)
    wizards.put(WizardState(admin_user_id=1))
    clock.advance(11)
    assert wizards.get(1) is None

    publishes = PublishStore(clock=clock, ttl_seconds=10)
    publishes.put(PublishSession(admin_user_id=1, poll_id=1))
    clock.advance(11)
    assert publishes.get(1) is None


async def test_check_database_success_and_execute_failure(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    assert await check_database(factory) is True

    class BadSession:
        async def __aenter__(self) -> BadSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, *args: object) -> None:
            raise OSError("boom")

    class BadFactory:
        def __call__(self) -> BadSession:
            return BadSession()

    assert await check_database(BadFactory()) is False  # type: ignore[arg-type]


async def test_publish_active_poll_and_no_wake(
    factory: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    from telegram_auto_poll.services.publish_service import PublishService

    service = PublishService(uow=lambda: unit_of_work(factory), clock=FakeClock(FIXED_NOW))
    first = await service.publish(actor_user_id=1, poll_id=seed.poll_id, chat_ids=[seed.chat_id])
    second = await service.publish(actor_user_id=1, poll_id=seed.poll_id, chat_ids=[seed.chat_id])
    assert first.poll.status.value == "active"
    assert second.poll.status.value == "active"


async def test_recovery_alerts_on_a_stale_row(
    factory: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    async with session_scope(factory) as session:
        await session.execute(
            text("UPDATE poll_messages SET status = 'sending' WHERE id = :id"),
            {"id": seed.message_id},
        )
    notifier = FakeNotifier()
    result = await RecoveryService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        notifier=notifier,
    ).run()
    assert result.failed_messages == 1
    assert len(notifier.messages) == 1


async def test_response_service_rejects_unknown_instance(
    factory: async_sessionmaker[AsyncSession], broadcaster: Broadcaster
) -> None:
    service = ResponseService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        broadcaster=broadcaster,  # type: ignore[arg-type]
    )
    with pytest.raises(ResponseRejectedError, match="no longer available"):
        await service.toggle(
            instance_id=999,
            option_id=1,
            telegram_user_id=1,
            first_name="Alice",
            chat_id=-1,
            message_id=1,
        )


async def test_retention_full_expired_batch(
    factory: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    from telegram_auto_poll.db.repositories.poll_instances import PollInstanceRepository
    from telegram_auto_poll.db.repositories.poll_responses import PollResponseRepository
    from telegram_auto_poll.domain.enums import InstanceStatus
    from tests.factories import make_instance

    old = FIXED_NOW - timedelta(days=100)
    async with session_scope(factory) as session:
        instance_repo = PollInstanceRepository(session)
        response_repo = PollResponseRepository(session)
        await response_repo.toggle(
            instance_id=seed.instance_id,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=1,
            first_name="Alice",
            now=old,
        )
        await instance_repo.set_status(
            seed.instance_id, InstanceStatus.CLOSED, now=old, closed_at=old
        )
        second = await instance_repo.add(make_instance(poll_id=seed.poll_id), now=old)
        await response_repo.toggle(
            instance_id=second.id or 0,
            option_id=seed.poll.options[0].id or 0,
            telegram_user_id=2,
            first_name="Bob",
            now=old,
        )
        await instance_repo.set_status(
            second.id or 0, InstanceStatus.CLOSED, now=old, closed_at=old
        )

    async def redact(instance_ids: list[int]) -> BroadcastResult:
        return BroadcastResult()

    async def checkpoint() -> None:
        return None

    result = await RetentionService(
        uow=lambda: unit_of_work(factory),
        clock=FakeClock(FIXED_NOW),
        checkpoint=checkpoint,
        redact=redact,
        response_retention_days=30,
        batch_size=1,
    ).run()
    assert result.responses == 2
