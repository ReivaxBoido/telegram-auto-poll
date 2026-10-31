"""Admin command handlers (T-044, FR-109, FR-112 - FR-119, FR-123, FR-124, FR-132)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import InlineKeyboardMarkup

from telegram_auto_poll.bot.auth import AdminGuard, rate_limit_message
from telegram_auto_poll.bot.handlers.commands import (
    HandlerContext,
    addchat,
    cancel,
    chat_id_of,
    chats,
    delete_schedule,
    handle_callback,
    handle_command,
    help_command,
    newpoll,
    parse_int,
    pause,
    poll,
    polls,
    removechat,
    resume,
    schedule,
    schedules,
    sendnow,
    start,
    status,
    user_id_of,
    version,
)
from telegram_auto_poll.bot.wizards.states import WizardState, WizardStep
from telegram_auto_poll.bot.wizards.store import WizardStore
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import DeliveryStatus
from telegram_auto_poll.domain.errors import ValidationError
from telegram_auto_poll.services.chat_service import ChatService
from telegram_auto_poll.services.delivery_service import DeliveryService
from telegram_auto_poll.services.poll_service import PollService
from telegram_auto_poll.services.schedule_service import ScheduleService
from tests.conftest import ADMIN_USER_ID, Seed
from tests.factories import FIXED_NOW, make_chat, make_delivery
from tests.fakes import FakeChatGateway, FakeClock, FakeNotifier, FakePacer, FakeSender

CHAT = -1_001_234_567_890


class FakeResponder:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, InlineKeyboardMarkup | None]] = []
        self.answers: list[tuple[str, str]] = []

    async def send(
        self, chat_id: int, text: str, *, keyboard: InlineKeyboardMarkup | None = None
    ) -> None:
        self.sent.append((chat_id, text, keyboard))

    async def answer(self, callback_query_id: str, text: str) -> None:
        self.answers.append((callback_query_id, text))

    @property
    def last(self) -> str:
        return self.sent[-1][1]

    @property
    def text(self) -> str:
        return "\n".join(message for _, message, _ in self.sent)


def message(
    text: str | None, *, user_id: int = ADMIN_USER_ID, chat_type: str = "private"
) -> object:
    return SimpleNamespace(
        message=SimpleNamespace(
            text=text,
            chat=SimpleNamespace(id=CHAT, type=chat_type),
            from_user=SimpleNamespace(id=user_id),
        )
    )


def build(
    factory: async_sessionmaker[AsyncSession],
    *,
    heartbeat: float | None = 1.0,
) -> tuple[HandlerContext, FakeResponder, FakeClock, FakeChatGateway]:
    clock = FakeClock(FIXED_NOW)
    responder = FakeResponder()
    gateway = FakeChatGateway()
    notifier = FakeNotifier()
    delivery = DeliveryService(
        uow=lambda: unit_of_work(factory),
        sender=FakeSender(),
        notifier=notifier,
        pacer=FakePacer(),
        clock=clock,
        random_source=lambda: 0.5,
    )
    ctx = HandlerContext(
        guard=AdminGuard(admin_user_ids=frozenset({ADMIN_USER_ID}), clock=clock),
        chats=ChatService(
            uow=lambda: unit_of_work(factory), gateway=gateway, clock=clock, bot_user_id=1
        ),
        polls=PollService(uow=lambda: unit_of_work(factory), clock=clock),
        schedules=ScheduleService(uow=lambda: unit_of_work(factory), clock=clock),
        deliveries=delivery,
        uow=lambda: unit_of_work(factory),
        wizards=WizardStore(clock=clock),
        clock=clock,
        responder=responder,
        version="0.1.0",
        build_sha="abc1234",
        started_at=FIXED_NOW - timedelta(hours=1),
        default_timezone="UTC",
        heartbeat_age=lambda: heartbeat,
    )
    return ctx, responder, clock, gateway


# --------------------------------------------------------------------------------------------
# Admission
# --------------------------------------------------------------------------------------------


async def test_a_group_message_is_ignored_silently(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_command(message("/status", chat_type="group"), ctx)

    assert responder.sent == []


async def test_a_non_admin_receives_the_generic_refusal(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_command(message("/status", user_id=42), ctx)

    assert responder.last == "This is a private bot. Access is restricted to its administrators."


async def test_an_exhausted_budget_is_refused_with_the_wait(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    for _ in range(20):
        ctx.guard.consume_command(ADMIN_USER_ID, "status")

    await handle_command(message("/status"), ctx)

    assert responder.last == rate_limit_message(60)


async def test_the_update_helpers_reject_a_message_less_update() -> None:
    with pytest.raises(ValueError, match="no message"):
        chat_id_of(SimpleNamespace())
    assert user_id_of(SimpleNamespace(message=SimpleNamespace())) == 0


@pytest.mark.parametrize(
    ("value", "fragment"),
    [(None, "numeric"), ("x", "numeric"), ("0", "at least 1")],
)
def test_parse_int_rejects_bad_arguments(value: str | None, fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        parse_int(value, field="poll_id")


# --------------------------------------------------------------------------------------------
# Basic commands
# --------------------------------------------------------------------------------------------


async def test_start_and_help_identify_the_admin(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await start(message("/start"), ctx)
    await help_command(message("/help"), ctx)

    assert "admin id" in responder.text
    assert "/newpoll" in responder.text


async def test_version_reports_the_build(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await version(message("/version"), ctx)

    assert "0.1.0" in responder.last
    assert "abc1234" in responder.last


async def test_status_reports_counts_and_next_runs(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await status(message("/status"), ctx)

    assert "database: ok" in responder.last
    assert "chats: 1 active" in responder.last
    assert "polls: 1 active" in responder.last
    assert "next runs:" in responder.last


async def test_status_reports_recent_failures(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(factory) as repos:
        await repos.deliveries.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=seed.schedule_id,
                    status=DeliveryStatus.FAILED,
                    completed_at=FIXED_NOW,
                    last_error="chat not found",
                )
            ],
            now=FIXED_NOW,
        )
    ctx, responder, _, _ = build(factory)

    await status(message("/status"), ctx)

    assert "recent failures:" in responder.last
    assert "chat not found" in responder.last


async def test_status_without_a_heartbeat(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory, heartbeat=None)

    await status(message("/status"), ctx)

    assert "scheduler heartbeat: never" in responder.last


async def test_cancel_discards_an_active_wizard(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    ctx.wizards.put(WizardState(admin_user_id=ADMIN_USER_ID))

    await cancel(message("/cancel"), ctx)

    assert responder.last == "wizard cancelled"
    assert ctx.wizards.get(ADMIN_USER_ID) is None


async def test_cancel_without_a_wizard_says_so(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await cancel(message("/cancel"), ctx)

    assert "no wizard" in responder.last


# --------------------------------------------------------------------------------------------
# Chats
# --------------------------------------------------------------------------------------------


async def test_chats_lists_registered_rows(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await chats(message("/chats"), ctx)

    assert "Automation test group" in responder.last
    assert "Page 1/1" in responder.last
    assert responder.sent[-1][2] is not None


async def test_an_empty_chat_list_explains_addchat(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, responder, _, _ = build(factory)

    await chats(message("/chats"), ctx)

    assert "/addchat" in responder.last


async def test_chats_rejects_a_bad_page(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await chats(message("/chats nope"), ctx)

    assert "numeric page" in responder.last


async def test_addchat_accepts_a_forwarded_message(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, gateway = build(factory)
    gateway.add_chat(CHAT, title="Forwarded")
    gateway.members[(CHAT, 1)] = True
    update = message(None)
    update.message.forward_from_chat = SimpleNamespace(id=CHAT)

    await addchat(update, ctx)

    assert "registered" in responder.last
    assert "Forwarded" in responder.last


async def test_addchat_accepts_a_pasted_id(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, gateway = build(factory)
    gateway.add_chat(CHAT, title="Pasted")
    gateway.members[(CHAT, 1)] = True

    await addchat(message(f"/addchat {CHAT}"), ctx)

    assert "registered" in responder.last


async def test_addchat_warns_and_reactivates(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, gateway = build(factory)
    gateway.add_chat(CHAT, title="Ops")
    async with unit_of_work(factory) as repos:
        chat = await repos.chats.upsert(make_chat(telegram_chat_id=CHAT), now=FIXED_NOW)
        assert chat.id is not None
        await repos.chats.set_active(chat.id, active=False, now=FIXED_NOW)

    await addchat(message(f"/addchat {CHAT}"), ctx)

    assert "reactivated" in responder.last
    assert "not an administrator" in responder.last


async def test_addchat_reports_bad_input(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await addchat(message("/addchat nonsense"), ctx)

    assert "numeric chat id" in responder.last


async def test_addchat_reports_an_unknown_chat(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, gateway = build(factory)
    gateway.missing.add(CHAT)

    await addchat(message(f"/addchat {CHAT}"), ctx)

    assert "does not expose" in responder.last


async def test_removechat_requires_a_valid_id(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await removechat(message("/removechat"), ctx)

    assert "numeric chat_id" in responder.last


async def test_removechat_reports_an_unknown_chat(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await removechat(message("/removechat 999"), ctx)

    assert "not registered" in responder.last


async def test_removechat_shows_a_confirmation(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await removechat(message(f"/removechat {seed.chat_id}"), ctx)

    assert "deactivate" in responder.last
    assert responder.sent[-1][2] is not None


# --------------------------------------------------------------------------------------------
# Polls
# --------------------------------------------------------------------------------------------


async def test_polls_lists_and_paginates(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await polls(message("/polls"), ctx)

    assert "What should we ship next?" in responder.last
    assert "Page 1/1" in responder.last


async def test_an_empty_poll_list_explains_newpoll(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, responder, _, _ = build(factory)

    await polls(message("/polls"), ctx)

    assert "/newpoll" in responder.last


async def test_polls_rejects_a_bad_page(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await polls(message("/polls x"), ctx)

    assert "numeric page" in responder.last


async def test_poll_shows_the_detail(seed: Seed, factory: async_sessionmaker[AsyncSession]) -> None:
    ctx, responder, _, _ = build(factory)

    await poll(message(f"/poll {seed.poll_id}"), ctx)

    assert f"Poll #{seed.poll_id}" in responder.last
    assert "schedules:" in responder.last


async def test_poll_rejects_a_bad_id(seed: Seed, factory: async_sessionmaker[AsyncSession]) -> None:
    ctx, responder, _, _ = build(factory)

    await poll(message("/poll nope"), ctx)

    assert "numeric poll_id" in responder.last


async def test_poll_reports_a_missing_poll(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await poll(message("/poll 999"), ctx)

    assert "does not exist" in responder.last


async def test_sendnow_shows_a_confirmation(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await sendnow(message(f"/sendnow {seed.poll_id}"), ctx)

    assert "send poll" in responder.last
    assert responder.sent[-1][2] is not None


async def test_sendnow_rejects_bad_input(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await sendnow(message("/sendnow nope"), ctx)
    await sendnow(message("/sendnow 999"), ctx)

    assert "numeric poll_id" in responder.text
    assert "does not exist" in responder.text


# --------------------------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------------------------


async def test_schedules_lists_and_filters(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await schedules(message("/schedules"), ctx)
    await schedules(message(f"/schedules {seed.poll_id}"), ctx)

    assert "daily" in responder.text
    assert "Page 1/1" in responder.text


async def test_an_empty_schedule_list_explains_newpoll(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, responder, _, _ = build(factory)

    await schedules(message("/schedules"), ctx)

    assert "/newpoll" in responder.last


async def test_schedules_rejects_a_bad_filter(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await schedules(message("/schedules poll"), ctx)

    assert "numeric poll_id" in responder.last


async def test_schedule_shows_the_detail_and_next_runs(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await schedule(message(f"/schedule {seed.schedule_id}"), ctx)

    assert "next:" in responder.last


async def test_schedule_rejects_bad_input(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await schedule(message("/schedule nope"), ctx)
    await schedule(message("/schedule 999"), ctx)

    assert "numeric schedule_id" in responder.text
    assert "does not exist" in responder.text


async def test_pause_and_resume(seed: Seed, factory: async_sessionmaker[AsyncSession]) -> None:
    ctx, responder, _, _ = build(factory)

    await pause(message(f"/pause {seed.schedule_id}"), ctx)
    assert "paused" in responder.last

    await resume(message(f"/resume {seed.schedule_id}"), ctx)
    assert "daily" in responder.last


async def test_pause_and_resume_report_errors(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await pause(message("/pause nope"), ctx)
    await resume(message("/resume 999"), ctx)

    assert "numeric schedule_id" in responder.text
    assert "does not exist" in responder.text


async def test_delete_schedule_shows_a_confirmation(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await delete_schedule(message(f"/delete_schedule {seed.schedule_id}"), ctx)

    assert "delete schedule" in responder.last
    assert responder.sent[-1][2] is not None


async def test_delete_schedule_reports_bad_input(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await delete_schedule(message("/delete_schedule nope"), ctx)
    await delete_schedule(message("/delete_schedule 999"), ctx)

    assert "numeric schedule_id" in responder.text
    assert "does not exist" in responder.text


async def test_an_unknown_command_receives_a_hint(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_command(message("/banana"), ctx)

    assert "unknown command" in responder.last


EVERY_HANDLER = [
    newpoll,
    start,
    help_command,
    version,
    status,
    cancel,
    chats,
    addchat,
    removechat,
    polls,
    poll,
    sendnow,
    schedules,
    schedule,
    pause,
    resume,
    delete_schedule,
]


@pytest.mark.parametrize("handler", EVERY_HANDLER)
async def test_every_handler_refuses_a_non_admin(
    handler: object, seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handler(message("/x", user_id=42), ctx)  # type: ignore[operator]

    assert responder.last == "This is a private bot. Access is restricted to its administrators."


def test_argument_of_a_message_without_text_is_none() -> None:
    from telegram_auto_poll.bot.handlers.commands import argument

    assert argument(SimpleNamespace(message=SimpleNamespace(text=None))) is None


async def test_an_unknown_command_from_a_group_stays_silent(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_command(message("/banana", chat_type="group"), ctx)

    assert responder.sent == []


# --------------------------------------------------------------------------------------------
# New-poll wizard
# --------------------------------------------------------------------------------------------


def callback(data: str, *, user_id: int = ADMIN_USER_ID, query_id: str = "cb-1") -> object:
    return SimpleNamespace(
        callback_query=SimpleNamespace(
            id=query_id,
            from_user=SimpleNamespace(id=user_id),
            data=data,
            message=SimpleNamespace(chat=SimpleNamespace(id=CHAT, type="private")),
        )
    )


async def drive(ctx: HandlerContext, *texts: str) -> None:
    for value in texts:
        await handle_command(message(value), ctx)


async def test_newpoll_starts_a_session(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_command(message("/newpoll"), ctx)

    assert "poll question" in responder.last
    assert ctx.wizards.get(ADMIN_USER_ID) is not None


async def test_newpoll_reports_an_open_session(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    await handle_command(message("/newpoll"), ctx)

    await handle_command(message("/newpoll"), ctx)

    assert "already open" in responder.last


async def test_the_wizard_creates_the_poll_end_to_end(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await drive(
        ctx,
        "/newpoll",
        "What should we ship next?",
        "skip",
        "Speed",
        "Docs",
        "done",
        "toggle",
        "next",
        "toggle",
        "next",
        str(seed.chat_id),
        "next",
        "daily",
        "09:00",
        "skip",
        "skip",
        "skip",
        "confirm",
    )

    assert "created poll #" in responder.last
    assert ctx.wizards.get(ADMIN_USER_ID) is None
    async with unit_of_work(factory) as repos:
        assert await repos.polls.count() == 2
        assert await repos.schedules.count() == 2


async def test_invalid_wizard_input_re_prompts_without_losing_answers(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    await handle_command(message("/newpoll"), ctx)

    await handle_command(message(""), ctx)

    assert "1-300" in responder.last
    session = ctx.wizards.get(ADMIN_USER_ID)
    assert session is not None
    assert session.step is WizardStep.QUESTION


async def test_back_preserves_the_question(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, _, _, _ = build(factory)
    await handle_command(message("/newpoll"), ctx)
    await handle_command(message("First wording?"), ctx)

    await handle_command(message("back"), ctx)

    session = ctx.wizards.get(ADMIN_USER_ID)
    assert session is not None
    assert session.step is WizardStep.QUESTION
    assert session.question == "First wording?"


async def test_plain_text_without_a_session_gets_a_hint(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_command(message("hello"), ctx)

    assert "unknown command" in responder.last


async def test_a_wizard_can_be_cancelled_with_text(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    await handle_command(message("/newpoll"), ctx)

    await handle_command(message("cancel"), ctx)

    assert responder.last == "wizard cancelled"
    assert ctx.wizards.get(ADMIN_USER_ID) is None


async def test_a_wizard_can_be_cancelled_with_the_callback(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    await handle_command(message("/newpoll"), ctx)

    await handle_callback(callback("v1:w:cancel:chat"), ctx)

    assert ctx.wizards.get(ADMIN_USER_ID) is None
    assert responder.answers[-1] == ("cb-1", "cancelled")


async def test_committing_with_an_inactive_target_keeps_the_session(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    await drive(
        ctx,
        "/newpoll",
        "Atomic?",
        "skip",
        "A",
        "B",
        "done",
        "next",
        "next",
        str(seed.chat_id),
        "next",
        "daily",
        "09:00",
        "skip",
        "skip",
        "skip",
    )
    async with unit_of_work(factory) as repos:
        await repos.chats.set_active(seed.chat_id, active=False, now=FIXED_NOW)

    await handle_command(message("confirm"), ctx)

    assert "not created" in responder.last
    session = ctx.wizards.get(ADMIN_USER_ID)
    assert session is not None
    assert session.step is WizardStep.PREVIEW


# --------------------------------------------------------------------------------------------
# Callback actions
# --------------------------------------------------------------------------------------------


async def test_removechat_confirmation_deactivates_the_chat(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback(f"v1:w:confirm:chat_{seed.chat_id}"), ctx)

    assert "deactivated" in responder.last
    async with unit_of_work(factory) as repos:
        chat = await repos.chats.get(seed.chat_id)
    assert chat is not None
    assert chat.is_active is False


async def test_delete_schedule_confirmation_deletes_only_the_schedule(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback(f"v1:w:confirm:schedule_{seed.schedule_id}"), ctx)

    assert "deleted" in responder.last
    async with unit_of_work(factory) as repos:
        assert await repos.schedules.get(seed.schedule_id) is None
        assert await repos.polls.get(seed.poll_id) is not None


async def test_sendnow_confirmation_queues_manual_deliveries(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback(f"v1:w:confirm:send_{seed.poll_id}"), ctx)

    assert "queued 1" in responder.last
    async with unit_of_work(factory) as repos:
        counts = await repos.deliveries.counts()
    assert counts[DeliveryStatus.PENDING] == 1


async def test_a_non_admin_callback_changes_nothing(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback(f"v1:w:confirm:chat_{seed.chat_id}", user_id=42), ctx)

    assert responder.answers[-1] == ("cb-1", "not allowed")
    async with unit_of_work(factory) as repos:
        chat = await repos.chats.get(seed.chat_id)
    assert chat is not None
    assert chat.is_active is True


async def test_a_forged_namespace_callback_is_refused(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback("v1:x:confirm:chat_1"), ctx)

    assert responder.answers[-1] == ("cb-1", "not allowed")


async def test_a_malformed_confirmation_is_refused(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback("v1:w:confirm:banana_1"), ctx)
    await handle_callback(callback("v1:w:confirm:chat_north"), ctx)

    assert responder.answers == [("cb-1", "invalid action"), ("cb-1", "invalid action")]


async def test_an_unsupported_callback_is_answered(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback("v1:p:view:1"), ctx)

    assert responder.answers[-1] == ("cb-1", "unsupported action")


async def test_a_failing_confirmation_reports_the_error(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback("v1:w:confirm:chat_999"), ctx)

    assert responder.answers[-1] == ("cb-1", "failed")
    assert "not registered" in responder.last


async def test_removing_an_option_from_the_session(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    ctx.wizards.put(
        WizardState(admin_user_id=ADMIN_USER_ID, step=WizardStep.OPTIONS, options=("A", "B"))
    )

    await handle_callback(callback("v1:w:option_remove:0"), ctx)

    session = ctx.wizards.get(ADMIN_USER_ID)
    assert session is not None
    assert session.options == ("B",)
    assert responder.answers[-1] == ("cb-1", "removed")


async def test_removing_an_option_outside_the_options_step_is_refused(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback("v1:w:option_remove:0"), ctx)

    ctx.wizards.put(
        WizardState(admin_user_id=ADMIN_USER_ID, step=WizardStep.OPTIONS, options=("A",))
    )
    await handle_callback(callback("v1:w:option_remove:5"), ctx)

    assert responder.answers == [
        ("cb-1", "the wizard is no longer on options"),
        ("cb-1", "invalid option"),
    ]


def test_every_wizard_step_has_instructions() -> None:
    from telegram_auto_poll.bot.handlers.commands import render_step
    from telegram_auto_poll.bot.wizards.engine import WizardContext

    context = WizardContext(
        default_timezone="UTC", active_chat_ids=frozenset({CHAT}), now=FIXED_NOW
    )
    for step in WizardStep:
        rendered = render_step(WizardState(admin_user_id=ADMIN_USER_ID, step=step), context)
        assert rendered


async def test_a_non_admin_plain_text_in_a_session_is_refused(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    await handle_command(message("/newpoll"), ctx)

    await handle_command(message("hello", user_id=42), ctx)

    assert responder.last == "This is a private bot. Access is restricted to its administrators."


async def test_a_wizard_message_without_a_session_is_reported(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    from telegram_auto_poll.bot.handlers.commands import handle_wizard_message

    ctx, responder, _, _ = build(factory)

    await handle_wizard_message(message("hello"), ctx)

    assert "no wizard is open" in responder.last


async def test_a_finished_wizard_ignores_further_text(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    ctx.wizards.put(WizardState(admin_user_id=ADMIN_USER_ID, step=WizardStep.COMPLETED))

    await handle_command(message("hello"), ctx)

    assert "already finished" in responder.last


async def test_callback_data_that_fails_decoding_is_answered(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)

    await handle_callback(callback("v1:p::1"), ctx)

    assert responder.answers[-1] == ("cb-1", "invalid action")


async def test_a_non_admin_with_a_session_is_refused_before_the_wizard_runs(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    ctx.wizards.put(WizardState(admin_user_id=42))

    await handle_command(message("hello", user_id=42), ctx)

    assert responder.last == "This is a private bot. Access is restricted to its administrators."
    assert ctx.wizards.get(42) is not None


async def test_an_unexpected_error_is_reported_with_a_correlation_id(
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, responder, _, _ = build(factory)

    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database on fire")

    monkeypatch.setattr(type(ctx.chats), "page", explode)

    await handle_command(message("/chats"), ctx)

    assert "unexpected error (reference " in responder.last


async def test_an_unexpected_error_without_a_chat_is_not_replied_to(
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, responder, _, _ = build(factory)

    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    from telegram_auto_poll.bot.handlers import commands

    monkeypatch.setattr(commands, "_route", explode)

    await handle_command(SimpleNamespace(message=None), ctx)

    assert responder.sent == []
