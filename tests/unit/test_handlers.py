"""Admin command and callback handlers (SPEC-003 §8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.bot.auth import AdminGuard
from telegram_auto_poll.bot.handlers.commands import (
    HandlerContext,
    handle_callback,
    handle_command,
)
from telegram_auto_poll.bot.publish_store import PublishStore
from telegram_auto_poll.bot.wizards.store import WizardStore
from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.poll_responses import PollResponseRepository
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.services.broadcaster import ResponseBroadcaster
from telegram_auto_poll.services.chat_service import ChatService
from telegram_auto_poll.services.poll_service import PollService
from telegram_auto_poll.services.posting_worker import PostingWorker
from telegram_auto_poll.services.publish_service import PublishService
from telegram_auto_poll.services.response_service import ResponseService
from telegram_auto_poll.services.status_service import StatusService
from tests.conftest import ADMIN_USER_ID, Seed
from tests.factories import FIXED_NOW
from tests.fakes import (
    FakeChatGateway,
    FakeClock,
    FakeMessageGateway,
    FakeNotifier,
    FakePacer,
    FakeSleeper,
)

ADMIN = ADMIN_USER_ID


@dataclass
class Responder:
    sends: list[dict[str, Any]] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)

    async def send(self, chat_id: int, text: str, *, keyboard: object = None) -> None:
        self.sends.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})

    async def edit(
        self, chat_id: int, message_id: int, text: str, *, keyboard: object = None
    ) -> None:
        self.edits.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text, "keyboard": keyboard}
        )

    async def answer(self, callback_query_id: str, text: str) -> None:
        self.answers.append({"id": callback_query_id, "text": text})


def _message(text: str, *, user_id: int = ADMIN, chat_type: str = "private") -> object:
    return SimpleNamespace(
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=chat_type),
            from_user=SimpleNamespace(id=user_id, first_name="Alice"),
            text=text,
        )
    )


def _callback(
    data: str,
    *,
    user_id: int = ADMIN,
    chat_id: int = ADMIN,
    message_id: int | None = 10,
    chat_type: str = "private",
    first_name: str = "Alice",
    inline_message_id: str | None = None,
) -> object:
    message = (
        SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=chat_type),
            message_id=message_id,
        )
        if message_id is not None
        else None
    )
    query = SimpleNamespace(
        id="cb",
        from_user=SimpleNamespace(id=user_id, first_name=first_name),
        data=data,
        message=message,
        inline_message_id=inline_message_id,
    )
    return SimpleNamespace(callback_query=query)


async def _true() -> bool:
    return True


@pytest_asyncio.fixture
async def harness(
    factory: async_sessionmaker[AsyncSession], seed: Seed
) -> tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway]:
    gateway = FakeMessageGateway()
    chat_gateway = FakeChatGateway()
    chat_gateway.add_chat(seed.chat.telegram_chat_id, title=seed.chat.title)
    chat_gateway.members[(seed.chat.telegram_chat_id, 123456789)] = True
    notifier = FakeNotifier()
    clock = FakeClock(FIXED_NOW)
    uow = lambda: unit_of_work(factory)  # noqa: E731
    broadcaster = ResponseBroadcaster(
        uow=uow,
        gateway=gateway,
        clock=clock,
        sleeper=FakeSleeper(),
        names_per_option=10,
        debounce_seconds=0,
    )
    posting = PostingWorker(
        uow=uow,
        gateway=gateway,
        pacer=FakePacer(),
        notifier=notifier,
        clock=clock,
        sleeper=FakeSleeper().sleep,
        names_per_option=10,
        max_attempts=2,
        retry_base_seconds=0,
        concurrency=2,
    )
    responder = Responder()
    ctx = HandlerContext(
        guard=AdminGuard(
            admin_user_ids=frozenset({ADMIN}),
            clock=clock,
            commands_per_minute=50,
            votes_per_minute=30,
        ),
        chats=ChatService(
            uow=uow,
            gateway=chat_gateway,
            clock=clock,
            bot_user_id=123456789,
        ),
        polls=PollService(uow=uow, clock=clock, broadcaster=broadcaster),
        publish=PublishService(uow=uow, clock=clock, wake=posting.wake),
        responses=ResponseService(uow=uow, clock=clock, broadcaster=broadcaster),
        status=StatusService(uow=uow),
        uow=uow,
        wizards=WizardStore(clock=clock),
        publish_sessions=PublishStore(clock=clock),
        clock=clock,
        responder=responder,
        posting=posting,
        version="0.3.0",
        build_sha="abc",
        started_at=FIXED_NOW,
        heartbeat_age=lambda: 1.0,
        db_check=_true,
    )
    return ctx, responder, gateway, chat_gateway


async def test_non_admin_is_refused_and_group_message_is_ignored(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/start", user_id=999), ctx)
    assert responder.sends[0]["text"].startswith("This is a private bot")

    await handle_command(_message("/start", chat_type="supergroup"), ctx)
    assert len(responder.sends) == 1


async def test_basic_commands(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    for command in ("/start", "/help", "/version", "/status"):
        await handle_command(_message(command), ctx)
    assert "telegram-auto-poll 0.3.0" in responder.sends[0]["text"]
    assert "Commands:" in responder.sends[1]["text"]
    assert "version: 0.3.0" in responder.sends[2]["text"]
    assert "polls:" in responder.sends[3]["text"]


async def test_unknown_command_and_plain_text_without_wizard(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/nope"), ctx)
    await handle_command(_message("hello"), ctx)
    assert "unknown command" in responder.sends[0]["text"]
    assert "unknown command" in responder.sends[1]["text"]


async def test_chats_addchat_and_removechat(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/chats"), ctx)
    assert "Automation test group" in responder.sends[0]["text"]

    await handle_command(_message(f"/addchat {seed.chat.telegram_chat_id}"), ctx)
    assert "registered" in responder.sends[1]["text"]

    await handle_command(_message(f"/removechat {seed.chat_id}"), ctx)
    assert "deactivate" in responder.sends[2]["text"]
    confirm = responder.sends[2]["keyboard"].inline_keyboard[0][0].callback_data
    await handle_callback(_callback(confirm), ctx)
    assert "deactivated" in responder.sends[3]["text"]


async def test_addchat_invalid_id(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/addchat 123"), ctx)
    assert "paste the numeric chat id" in responder.sends[0]["text"]


async def test_polls_and_poll_detail(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/polls"), ctx)
    assert "Saturday football" in responder.sends[0]["text"]

    await handle_command(_message(f"/poll {seed.poll_id}"), ctx)
    assert "Poll #" in responder.sends[1]["text"]
    assert responder.sends[1]["keyboard"] is not None


async def test_newpoll_wizard_commits(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/newpoll"), ctx)
    assert "Send the poll title" in responder.sends[0]["text"]

    await handle_command(_message("Game"), ctx)
    await handle_command(_message("In"), ctx)
    await handle_command(_message("Out"), ctx)
    await handle_command(_message("done"), ctx)
    assert "Preview" in responder.sends[-1]["text"]

    await handle_callback(_callback("v1:w:confirm"), ctx)
    assert "created poll" in responder.sends[-1]["text"]


async def test_newpoll_validation_remove_back_and_cancel(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/newpoll"), ctx)
    await handle_command(_message(""), ctx)
    assert "title must be 1-300" in responder.sends[-1]["text"]

    await handle_command(_message("Game"), ctx)
    await handle_command(_message("In"), ctx)
    await handle_command(_message("In"), ctx)
    assert "unique" in responder.sends[-1]["text"]

    await handle_callback(_callback("v1:w:option_remove:0"), ctx)
    assert "removed" in responder.answers[-1]["text"]
    await handle_callback(_callback("v1:w:option_remove:9"), ctx)
    assert "no longer exists" in responder.answers[-1]["text"]

    await handle_callback(_callback("v1:w:back"), ctx)
    assert "Send the poll title" in responder.sends[-1]["text"]

    await handle_callback(_callback("v1:w:cancel"), ctx)
    assert responder.answers[-1]["text"] == "cancelled"


async def test_wizard_rejects_a_second_session_and_expired_callback(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/newpoll"), ctx)
    await handle_command(_message("/newpoll"), ctx)
    assert "already open" in responder.sends[-1]["text"]

    await handle_callback(_callback("v1:w:confirm", user_id=999), ctx)
    assert responder.answers[-1]["text"] == "not allowed"


async def test_publish_flow_with_target_selection(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message(f"/publish {seed.poll_id}"), ctx)
    assert "Publish poll" in responder.sends[-1]["text"]

    toggle = f"v1:t:toggle:{seed.poll_id}_{seed.chat_id}"
    await handle_callback(_callback(toggle), ctx)
    assert "selected" in responder.answers[-1]["text"]
    assert responder.edits[-1]["message_id"] == 10

    await handle_callback(_callback(f"v1:t:page:{seed.poll_id}_1"), ctx)
    assert "page 1" in responder.answers[-1]["text"]

    await handle_callback(_callback(f"v1:t:done:{seed.poll_id}"), ctx)
    assert "confirm to publish" in responder.answers[-1]["text"]

    await handle_callback(_callback(f"v1:c:publish:{seed.poll_id}"), ctx)
    assert "published" in responder.answers[-1]["text"]


async def test_publish_requires_selection_and_can_cancel(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message(f"/publish {seed.poll_id}"), ctx)
    await handle_callback(_callback(f"v1:t:done:{seed.poll_id}"), ctx)
    assert "select at least one" in responder.answers[-1]["text"]

    await handle_callback(_callback(f"v1:t:cancel:{seed.poll_id}"), ctx)
    assert responder.answers[-1]["text"] == "cancelled"
    assert "publish cancelled" in responder.edits[-1]["text"]


async def test_target_callback_without_session(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_callback(_callback("v1:t:toggle:1_1"), ctx)
    assert "start with /publish" in responder.answers[-1]["text"]


async def test_close_reopen_refresh_and_delete(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message(f"/close {seed.poll_id}"), ctx)
    await handle_callback(_callback(f"v1:c:close:{seed.poll_id}"), ctx)
    assert "closed instance" in responder.sends[-1]["text"]

    await handle_command(_message(f"/reopen {seed.poll_id}"), ctx)
    await handle_callback(_callback(f"v1:c:reopen:{seed.poll_id}"), ctx)
    assert "reopened instance" in responder.sends[-1]["text"]

    await handle_command(_message(f"/refresh {seed.poll_id}"), ctx)
    assert "refreshed instance" in responder.sends[-1]["text"]

    await handle_command(_message(f"/deletepoll {seed.poll_id}"), ctx)
    await handle_callback(_callback(f"v1:c:delete:{seed.poll_id}"), ctx)
    assert "deleted poll" in responder.sends[-1]["text"]


async def test_poll_action_buttons(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_callback(_callback(f"v1:p:pub:{seed.poll_id}"), ctx)
    assert "choose chats" in responder.answers[-1]["text"]

    await handle_callback(_callback(f"v1:p:close:{seed.poll_id}"), ctx)
    assert responder.answers[-1]["text"] == "confirm"

    await handle_callback(_callback(f"v1:p:ref:{seed.poll_id}"), ctx)
    assert responder.answers[-1]["text"] == "refreshed"

    await handle_callback(_callback(f"v1:p:resp:{seed.poll_id}"), ctx)
    assert responder.answers[-1]["text"] == "responses"

    await handle_callback(_callback(f"v1:p:nope:{seed.poll_id}"), ctx)
    assert responder.answers[-1]["text"] == "unsupported action"


async def test_responses_command_and_pagination(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, responder, _, _ = harness
    async with session_scope(factory) as session:
        repo = PollResponseRepository(session)
        for index in range(25):
            await repo.toggle(
                instance_id=seed.instance_id,
                option_id=seed.poll.options[0].id or 0,
                telegram_user_id=index + 1,
                first_name=f"Name {index}",
                now=FIXED_NOW,
            )

    await handle_command(_message(f"/responses {seed.poll_id}"), ctx)
    assert "page 1/2" in responder.sends[-1]["text"]
    next_data = responder.sends[-1]["keyboard"].inline_keyboard[0][0].callback_data
    await handle_callback(_callback(next_data), ctx)
    assert "responses" in responder.answers[-1]["text"]


async def test_vote_callback_adds_and_removes(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _gateway, _ = harness
    data = f"v1:v:toggle:{seed.instance_id}_{seed.poll.options[0].id}"
    callback = _callback(
        data,
        user_id=42,
        chat_id=seed.chat.telegram_chat_id,
        message_id=seed.message.telegram_message_id,
        chat_type="supergroup",
    )

    await handle_callback(callback, ctx)
    assert "Added to" in responder.answers[-1]["text"]

    await handle_callback(callback, ctx)
    assert "Removed from" in responder.answers[-1]["text"]


async def test_vote_callback_rejections(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    closed = _callback(
        f"v1:v:toggle:{seed.instance_id}_{seed.poll.options[0].id}",
        user_id=42,
        chat_id=seed.chat.telegram_chat_id,
        message_id=seed.message.telegram_message_id,
        chat_type="supergroup",
    )
    await handle_command(_message(f"/close {seed.poll_id}"), ctx)
    await handle_callback(_callback(f"v1:c:close:{seed.poll_id}"), ctx)
    await handle_callback(closed, ctx)
    assert responder.answers[-1]["text"] == "Voting is closed"

    bad = _callback("v1:v:toggle:bad", user_id=42, chat_type="supergroup")
    await handle_callback(bad, ctx)
    assert responder.answers[-1]["text"] == "invalid action"

    missing = _callback(
        f"v1:v:toggle:{seed.instance_id}_999",
        user_id=42,
        chat_id=seed.chat.telegram_chat_id,
        message_id=seed.message.telegram_message_id,
        chat_type="supergroup",
    )
    await handle_command(_message(f"/reopen {seed.poll_id}"), ctx)
    await handle_callback(_callback(f"v1:c:reopen:{seed.poll_id}"), ctx)
    await handle_callback(missing, ctx)
    assert "option no longer exists" in responder.answers[-1]["text"]


async def test_admin_callback_in_group_is_refused(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_callback(_callback(f"v1:p:close:{seed.poll_id}", chat_type="supergroup"), ctx)
    assert responder.answers[-1]["text"] == "not allowed"


async def test_invalid_and_unsupported_callbacks(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_callback(_callback("bad"), ctx)
    assert responder.answers[-1]["text"] == "invalid action"

    await handle_callback(_callback("v1:p:close:notanint"), ctx)
    assert responder.answers[-1]["text"] == "invalid action"

    await handle_callback(_callback("v1:c:unknown:1"), ctx)
    assert responder.answers[-1]["text"] == "unsupported action"

    await handle_callback(_callback(f"v1:t:page:{seed.poll_id}_bad"), ctx)
    assert responder.answers[-1]["text"] == "start with /publish"


async def test_vote_rate_limit(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    ctx.guard._votes_per_minute = 1
    data = f"v1:v:toggle:{seed.instance_id}_{seed.poll.options[0].id}"
    callback = _callback(
        data,
        user_id=42,
        chat_id=seed.chat.telegram_chat_id,
        message_id=seed.message.telegram_message_id,
        chat_type="supergroup",
    )

    await handle_callback(callback, ctx)
    await handle_callback(callback, ctx)
    assert "Too many votes" in responder.answers[-1]["text"]


async def test_cancel_command_and_command_rate_limit(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/cancel"), ctx)
    assert "nothing to cancel" in responder.sends[-1]["text"]

    ctx.guard._commands_per_minute = 1
    await handle_command(_message("/help"), ctx)
    await handle_command(_message("/help"), ctx)
    assert "Too many commands" in responder.sends[-1]["text"]


async def test_unexpected_error_is_correlated(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, responder, _, _ = harness

    async def boom() -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(ctx.status, "snapshot", boom)
    await handle_command(_message("/status"), ctx)
    assert "unexpected error" in responder.sends[-1]["text"]


async def test_command_argument_helpers() -> None:
    from telegram_auto_poll.bot.handlers.commands import (
        argument,
        chat_id_of,
        message_text,
        parse_int,
    )
    from telegram_auto_poll.domain.errors import ValidationError

    assert message_text(object()) is None
    assert argument(object()) is None
    assert argument(_message("/help")) is None
    with pytest.raises(ValidationError, match="send a numeric"):
        parse_int(None, field="page")
    with pytest.raises(ValidationError, match="must be at least 1"):
        parse_int("0", field="page")
    with pytest.raises(ValueError, match="update has no message"):
        chat_id_of(object())


async def test_empty_and_invalid_listings(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/chats 99"), ctx)
    assert "Nothing here yet" in responder.sends[-1]["text"]
    await handle_command(_message("/chats 0"), ctx)
    assert "page must be at least 1" in responder.sends[-1]["text"]

    await handle_command(_message("/polls 99"), ctx)
    assert "Nothing here yet" in responder.sends[-1]["text"]
    await handle_command(_message("/polls 0"), ctx)
    assert "page must be at least 1" in responder.sends[-1]["text"]

    for command in ("/poll 999", "/close 999", "/reopen 999", "/deletepoll 999", "/refresh 999"):
        await handle_command(_message(command), ctx)
        assert "does not exist" in responder.sends[-1]["text"]

    await handle_command(_message("/poll abc"), ctx)
    assert "send a numeric poll_id" in responder.sends[-1]["text"]
    await handle_command(_message("/responses"), ctx)
    assert "send a numeric poll_id" in responder.sends[-1]["text"]
    await handle_command(_message(f"/responses {seed.poll_id} 9"), ctx)
    assert "option must be between" in responder.sends[-1]["text"]


async def test_addchat_forwarded_warning_and_reactivation(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, chat_gateway = harness
    chat_gateway.members[(seed.chat.telegram_chat_id, 123456789)] = False
    forwarded = SimpleNamespace(
        message=SimpleNamespace(
            chat=SimpleNamespace(id=ADMIN, type="private"),
            from_user=SimpleNamespace(id=ADMIN, first_name="Alice"),
            text="/addchat",
            forward_origin=SimpleNamespace(
                sender_chat=SimpleNamespace(id=seed.chat.telegram_chat_id)
            ),
        )
    )
    await handle_command(forwarded, ctx)
    assert "not an administrator" in responder.sends[-1]["text"]

    await handle_command(_message(f"/removechat {seed.chat_id}"), ctx)
    confirm = responder.sends[-1]["keyboard"].inline_keyboard[0][0].callback_data
    await handle_callback(_callback(confirm), ctx)
    await handle_command(forwarded, ctx)
    assert "reactivated" in responder.sends[-1]["text"]


async def test_removechat_invalid_and_unknown(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/removechat abc"), ctx)
    assert "send a numeric chat_id" in responder.sends[-1]["text"]
    await handle_command(_message("/removechat 999"), ctx)
    assert "is not registered" in responder.sends[-1]["text"]


async def test_wizard_no_session_ignored_and_terminal_rendering(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    from telegram_auto_poll.bot.handlers.commands import _render_step, handle_wizard_message
    from telegram_auto_poll.bot.wizards.states import WizardState, WizardStep

    ctx, responder, _, _ = harness
    await handle_wizard_message(_message("hello"), ctx)
    assert "no wizard is open" in responder.sends[-1]["text"]

    ctx.wizards.put(WizardState(admin_user_id=ADMIN, step=WizardStep.COMPLETED))
    await handle_command(_message("hello"), ctx)
    assert "already finished" in responder.sends[-1]["text"]

    assert (
        _render_step(WizardState(admin_user_id=ADMIN, step=WizardStep.CANCELLED))
        == "wizard cancelled"
    )
    assert (
        _render_step(WizardState(admin_user_id=ADMIN, step=WizardStep.COMPLETED))
        == "wizard complete"
    )


async def test_wizard_create_failure_is_reported(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, responder, _, _ = harness

    async def boom(**kwargs: object) -> object:
        from telegram_auto_poll.domain.errors import ValidationError

        raise ValidationError("title", "bad title")

    monkeypatch.setattr(ctx.polls, "create", boom)
    await handle_command(_message("/newpoll"), ctx)
    await handle_command(_message("Game"), ctx)
    await handle_command(_message("In"), ctx)
    await handle_command(_message("done"), ctx)
    await handle_callback(_callback("v1:w:confirm"), ctx)
    assert "not created" in responder.sends[-1]["text"]


async def test_wizard_callbacks_expired_invalid_and_unsupported(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_callback(_callback("v1:w:confirm"), ctx)
    assert "wizard expired" in responder.answers[-1]["text"]

    await handle_command(_message("/newpoll"), ctx)
    await handle_command(_message("Game"), ctx)
    await handle_command(_message("In"), ctx)
    await handle_callback(_callback("v1:w:option_remove:bad"), ctx)
    assert responder.answers[-1]["text"] == "invalid action"
    await handle_callback(_callback("v1:w:nope"), ctx)
    assert responder.answers[-1]["text"] == "unsupported action"


async def test_target_invalid_pairs_and_unsupported(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message(f"/publish {seed.poll_id}"), ctx)
    for data in (
        "v1:t:page:bad",
        "v1:t:toggle:bad",
        "v1:t:page:999_1",
        "v1:t:toggle:999_1",
        f"v1:t:nope:{seed.poll_id}",
    ):
        await handle_callback(_callback(data), ctx)
        assert responder.answers[-1]["text"] in {"invalid action", "unsupported action"}


async def test_poll_callback_invalid_and_unsupported(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_callback(_callback("v1:p:resp:bad"), ctx)
    assert responder.answers[-1]["text"] == "invalid action"
    await handle_callback(_callback("v1:p:pub:bad"), ctx)
    assert responder.answers[-1]["text"] == "invalid action"
    await handle_callback(_callback("v1:p:nope:1"), ctx)
    assert responder.answers[-1]["text"] == "unsupported action"


async def test_confirm_invalid_and_domain_errors(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_callback(_callback("v1:c:close:bad"), ctx)
    assert responder.answers[-1]["text"] == "invalid action"

    await handle_callback(_callback(f"v1:c:publish:{seed.poll_id}"), ctx)
    assert responder.answers[-1]["text"] == "start with /publish"

    await handle_callback(_callback("v1:c:removechat:999"), ctx)
    assert responder.answers[-1]["text"] == "failed"
    assert "is not registered" in responder.sends[-1]["text"]

    await handle_callback(_callback("v1:c:close:999"), ctx)
    assert responder.answers[-1]["text"] == "failed"
    assert "does not exist" in responder.sends[-1]["text"]


async def test_publish_confirm_domain_error(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message(f"/publish {seed.poll_id}"), ctx)
    await handle_callback(_callback(f"v1:t:toggle:{seed.poll_id}_{seed.chat_id}"), ctx)
    await handle_callback(_callback(f"v1:t:done:{seed.poll_id}"), ctx)

    async def boom(**kwargs: object) -> object:
        from telegram_auto_poll.domain.errors import ValidationError

        raise ValidationError("targets", "no target")

    monkeypatch.setattr(ctx.publish, "publish", boom)
    await handle_callback(_callback(f"v1:c:publish:{seed.poll_id}"), ctx)
    assert responder.answers[-1]["text"] == "failed"


async def test_vote_auth_denied_and_handle_command_without_message(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram_auto_poll.bot.auth import AuthDenial, Decision

    ctx, responder, _, _ = harness
    monkeypatch.setattr(
        ctx.guard,
        "authorize_vote_callback",
        lambda update: Decision(allowed=False, reason=AuthDenial.BAD_CALLBACK),
    )
    await handle_callback(_callback("v1:v:toggle:1_1", chat_type="supergroup"), ctx)
    assert responder.answers[-1]["text"] == "not allowed"

    await handle_command(object(), ctx)


async def test_status_with_database_failure(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    from dataclasses import replace

    async def false() -> bool:
        return False

    ctx, responder, _, _ = harness
    ctx = replace(ctx, db_check=false)
    await handle_command(_message("/status"), ctx)
    assert "database: unavailable" in responder.sends[-1]["text"]


async def test_all_commands_return_early_for_non_admins(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    from telegram_auto_poll.bot.handlers.commands import handle_wizard_message

    ctx, responder, _, _ = harness
    commands = [
        "/start",
        "/version",
        "/status",
        "/cancel",
        "/chats",
        "/addchat",
        "/removechat",
        "/polls",
        "/poll",
        "/close",
        "/reopen",
        "/deletepoll",
        "/refresh",
        "/responses",
        "/newpoll",
        "/publish",
    ]
    for command in commands:
        await handle_command(_message(command, user_id=999), ctx)
    await handle_wizard_message(_message("x", user_id=999), ctx)
    assert all("private bot" in send["text"] for send in responder.sends)


async def test_wizard_input_shortcuts_and_cancel_text(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    from telegram_auto_poll.bot.handlers.commands import _wizard_input
    from telegram_auto_poll.bot.wizards.states import WizardState, WizardStep

    state = WizardState(admin_user_id=ADMIN, step=WizardStep.OPTIONS)
    assert _wizard_input(state, "back").kind.value == "back"
    assert _wizard_input(state, "cancel").kind.value == "cancel"
    assert _wizard_input(state, "confirm").kind.value == "confirm"

    ctx, responder, _, _ = harness
    await handle_command(_message("/newpoll"), ctx)
    await handle_command(_message("Game"), ctx)
    await handle_command(_message("In"), ctx)
    await handle_command(_message("cancel"), ctx)
    assert responder.sends[-1]["text"] == "wizard cancelled"


async def test_wizard_option_done_callback(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/newpoll"), ctx)
    await handle_command(_message("Game"), ctx)
    await handle_command(_message("In"), ctx)
    await handle_callback(_callback("v1:w:option_done"), ctx)
    assert "Preview" in responder.sends[-1]["text"]
    assert responder.answers[-1]["text"] == "done"


async def test_publish_invalid_and_target_cancel_without_message(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    seed: Seed,
) -> None:
    ctx, responder, _, _ = harness
    await handle_command(_message("/publish 999"), ctx)
    assert "does not exist" in responder.sends[-1]["text"]
    await handle_command(_message("/publish abc"), ctx)
    assert "send a numeric poll_id" in responder.sends[-1]["text"]

    await handle_command(_message(f"/publish {seed.poll_id}"), ctx)
    no_message_id = SimpleNamespace(
        callback_query=SimpleNamespace(
            id="cb",
            from_user=SimpleNamespace(id=ADMIN, first_name="Alice"),
            data=f"v1:t:cancel:{seed.poll_id}",
            message=SimpleNamespace(
                chat=SimpleNamespace(id=ADMIN, type="private"), message_id=None
            ),
            inline_message_id=None,
        )
    )
    await handle_callback(no_message_id, ctx)
    assert responder.answers[-1]["text"] == "cancelled"


async def test_parse_helpers_reject_none() -> None:
    from telegram_auto_poll.bot.handlers.commands import _parse_pair, _parse_triple

    assert _parse_pair(None) is None
    assert _parse_triple(None) is None


async def test_handle_command_exception_without_message(
    harness: tuple[HandlerContext, Responder, FakeMessageGateway, FakeChatGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import telegram_auto_poll.bot.handlers.commands as commands

    ctx, _, _, _ = harness

    async def boom(update: object, ctx: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(commands, "_route", boom)
    await handle_command(object(), ctx)
