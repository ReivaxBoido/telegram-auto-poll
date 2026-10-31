"""Chat service behaviour (FR-112 - FR-115).

The service talks to a real SQLite file through the unit of work, and to the Telegram gateway
through ``FakeChatGateway``: no network, no sleeps, and every transaction boundary is observable in
the database afterwards.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.entities import ChatInfo
from telegram_auto_poll.domain.enums import AuditAction, ChatType, DeliveryStatus, SkipReason
from telegram_auto_poll.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from telegram_auto_poll.services.chat_service import ChatService, DeactivationResult, parse_chat_id
from tests.conftest import ADMIN_USER_ID, Seed
from tests.factories import FIXED_NOW, make_chat, make_delivery
from tests.fakes import FakeChatGateway, FakeClock

BOT_USER_ID = 777_000_111
CHAT_ID = -1_001_234_567_890
OTHER_CHAT_ID = -1_001_234_567_891


def build(
    factory: async_sessionmaker[AsyncSession],
    gateway: FakeChatGateway | None = None,
    clock: FakeClock | None = None,
) -> tuple[ChatService, FakeChatGateway, FakeClock]:
    resolved_gateway = gateway or FakeChatGateway()
    resolved_clock = clock or FakeClock(FIXED_NOW)
    service = ChatService(
        uow=lambda: unit_of_work(factory),
        gateway=resolved_gateway,
        clock=resolved_clock,
        bot_user_id=BOT_USER_ID,
    )
    return service, resolved_gateway, resolved_clock


def permitted_gateway(
    chat_type: ChatType = ChatType.SUPERGROUP,
    title: str = "Ops",
    *,
    chat_id: int = CHAT_ID,
) -> FakeChatGateway:
    gateway = FakeChatGateway()
    gateway.add_chat(chat_id, chat_type, title)
    gateway.members[(chat_id, BOT_USER_ID)] = True
    return gateway


# --------------------------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------------------------


async def test_register_stores_the_chat_and_audits_it(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _, _ = build(factory, permitted_gateway())

    result = await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)

    assert result.chat.telegram_chat_id == CHAT_ID
    assert result.chat.type is ChatType.SUPERGROUP
    assert result.chat.title == "Ops"
    assert result.chat.is_active is True
    assert result.reactivated is False
    assert result.warning is None

    async with unit_of_work(factory) as repos:
        assert await repos.chats.count() == 1
        events = await repos.audit.list_recent()
    assert [event.action for event in events] == [AuditAction.CHAT_REGISTER]
    assert events[0].entity_type == "chat"
    assert events[0].entity_id == result.chat.id


async def test_register_accepts_a_channel_when_the_bot_can_post(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _, _ = build(factory, permitted_gateway(ChatType.CHANNEL, "Announcements"))

    result = await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)

    assert result.chat.type is ChatType.CHANNEL
    assert result.warning is None


async def test_register_rejects_a_channel_without_posting_rights(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeChatGateway()
    gateway.add_chat(CHAT_ID, ChatType.CHANNEL, "Announcements")
    service, _, _ = build(factory, gateway)

    with pytest.raises(PermissionDeniedError, match="administrator"):
        await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)

    async with unit_of_work(factory) as repos:
        assert await repos.chats.count() == 0


async def test_register_warns_but_stores_a_group_without_admin_rights(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeChatGateway()
    gateway.add_chat(CHAT_ID, ChatType.GROUP, "Ops")
    service, _, _ = build(factory, gateway)

    result = await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)

    assert result.warning is not None
    assert "not an administrator" in result.warning
    assert result.chat.is_active is True


async def test_register_reports_an_unknown_chat(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeChatGateway()
    gateway.missing.add(CHAT_ID)
    service, _, _ = build(factory, gateway)

    with pytest.raises(NotFoundError, match="does not expose"):
        await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)


async def test_register_rejects_a_private_chat(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeChatGateway()
    gateway.chats[CHAT_ID] = ChatInfo(chat_id=CHAT_ID, type="private", title="DM")  # type: ignore[arg-type]
    service, _, _ = build(factory, gateway)

    with pytest.raises(ValidationError) as exc_info:
        await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)

    assert exc_info.value.field == "chat_type"


async def test_registering_twice_updates_the_title_without_duplicating(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = permitted_gateway()
    service, _, _ = build(factory, gateway)
    first = await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)
    gateway.chats[CHAT_ID] = ChatInfo(chat_id=CHAT_ID, type=ChatType.SUPERGROUP, title="Renamed")

    second = await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)

    assert second.chat.id == first.chat.id
    assert second.chat.title == "Renamed"
    assert second.reactivated is False
    async with unit_of_work(factory) as repos:
        assert await repos.chats.count() == 1


async def test_registering_a_removed_chat_reactivates_it(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _, _ = build(factory, permitted_gateway())
    first = await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)
    assert first.chat.id is not None
    await service.deactivate(first.chat.id, actor_user_id=ADMIN_USER_ID)

    result = await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)

    assert result.reactivated is True
    assert result.chat.is_active is True
    assert result.chat.id == first.chat.id


# --------------------------------------------------------------------------------------------
# Deactivation
# --------------------------------------------------------------------------------------------


async def test_deactivate_skips_pending_deliveries_and_keeps_history(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _, _ = build(factory, permitted_gateway(chat_id=seed.chat.telegram_chat_id))
    async with unit_of_work(factory) as repos:
        await repos.deliveries.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=seed.schedule_id,
                )
            ],
            now=FIXED_NOW,
        )

    result = await service.deactivate(seed.chat_id, actor_user_id=ADMIN_USER_ID)

    assert isinstance(result, DeactivationResult)
    assert result.chat.is_active is False
    assert result.skipped_deliveries == 1
    assert result.paused_schedules == (seed.schedule_id,)
    async with unit_of_work(factory) as repos:
        deliveries = await repos.deliveries.list_for_occurrence(
            seed.schedule_id, seed.schedule.next_run_at or FIXED_NOW
        )
        assert deliveries[0].status is DeliveryStatus.SKIPPED
        assert deliveries[0].skip_reason == SkipReason.CHAT_INACTIVE.value
        schedule = await repos.schedules.get(seed.schedule_id)
        assert schedule is not None
        assert schedule.is_paused is True
        assert schedule.next_run_at is None
        actions = [event.action for event in await repos.audit.list_recent()]
    assert AuditAction.CHAT_DEACTIVATE in actions
    assert AuditAction.SCHEDULE_PAUSE in actions


async def test_deactivate_is_idempotent(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _, _ = build(factory, permitted_gateway(chat_id=seed.chat.telegram_chat_id))
    await service.deactivate(seed.chat_id, actor_user_id=ADMIN_USER_ID)

    second = await service.deactivate(seed.chat_id, actor_user_id=ADMIN_USER_ID)

    assert second.skipped_deliveries == 0
    assert second.paused_schedules == ()
    async with unit_of_work(factory) as repos:
        assert len(await repos.audit.list_recent(limit=50)) == 2


async def test_deactivate_reports_an_unknown_chat(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service, _, _ = build(factory)

    with pytest.raises(NotFoundError, match="not registered"):
        await service.deactivate(404, actor_user_id=ADMIN_USER_ID)


async def test_a_schedule_survives_while_another_target_is_active(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    service, _, _ = build(factory, permitted_gateway(chat_id=seed.chat.telegram_chat_id))
    async with unit_of_work(factory) as repos:
        other = await repos.chats.upsert(
            make_chat(id=None, telegram_chat_id=OTHER_CHAT_ID), now=FIXED_NOW
        )
        assert other.id is not None
        await repos.polls.set_targets(seed.poll_id, [seed.chat_id, other.id], now=FIXED_NOW)

    result = await service.deactivate(seed.chat_id, actor_user_id=ADMIN_USER_ID)

    assert result.paused_schedules == ()
    async with unit_of_work(factory) as repos:
        schedule = await repos.schedules.get(seed.schedule_id)
    assert schedule is not None
    assert schedule.is_paused is False
    assert schedule.next_run_at is not None


# --------------------------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------------------------


async def test_page_splits_registered_chats(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(factory) as repos:
        for index in range(9):
            await repos.chats.upsert(
                make_chat(id=None, telegram_chat_id=-1_000_000_000_000 - index),
                now=FIXED_NOW,
            )
    service, _, _ = build(factory)

    first = await service.page(1)
    second = await service.page(2)

    assert first.total == 9
    assert len(first.items) == 8
    assert first.pages == 2
    assert first.has_next is True
    assert second.has_previous is True
    assert second.has_next is False
    assert len(second.items) == 1


async def test_page_can_filter_to_active_chats(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(factory) as repos:
        active = await repos.chats.upsert(
            make_chat(id=None, telegram_chat_id=-1_000_000_000_001), now=FIXED_NOW
        )
        inactive = await repos.chats.upsert(
            make_chat(id=None, telegram_chat_id=-1_000_000_000_002), now=FIXED_NOW
        )
        assert inactive.id is not None
        await repos.chats.set_active(inactive.id, active=False, now=FIXED_NOW)
    service, _, _ = build(factory)

    page = await service.page(1, active_only=True)

    assert [chat.id for chat in page.items] == [active.id]


async def test_page_rejects_zero(factory: async_sessionmaker[AsyncSession]) -> None:
    service, _, _ = build(factory)

    with pytest.raises(ValueError, match="page numbers start at 1"):
        await service.page(0)


async def test_page_reports_the_clock_when_registering(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock(FIXED_NOW + timedelta(days=3))
    service, _, _ = build(factory, permitted_gateway(), clock)

    result = await service.register(CHAT_ID, actor_user_id=ADMIN_USER_ID)

    assert result.chat.created_at == FIXED_NOW + timedelta(days=3)


# --------------------------------------------------------------------------------------------
# Pasted ids
# --------------------------------------------------------------------------------------------


def test_parse_chat_id_accepts_a_supergroup_id() -> None:
    assert parse_chat_id(" -1001234567890 ") == -1_001_234_567_890


def test_parse_chat_id_accepts_a_legacy_group_id() -> None:
    assert parse_chat_id("-12345") == -12345


@pytest.mark.parametrize("value", ["", "abc", "123456789", "user", "-12", "+1001234"])
def test_parse_chat_id_rejects_anything_else(value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_chat_id(value)
    assert exc_info.value.field == "chat_id"
