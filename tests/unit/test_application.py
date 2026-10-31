"""PTB application wiring (T-046, FR-109, FR-110)."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import BotCommandScopeAllPrivateChats
from telegram.ext import CallbackQueryHandler, MessageHandler

from telegram_auto_poll.bot.application import (
    COMMAND_MENU,
    BotResponder,
    _register_commands,
    build_application,
    command_menu,
    private_command_scope,
    register_commands,
)
from telegram_auto_poll.bot.auth import AdminGuard
from telegram_auto_poll.bot.handlers.commands import HandlerContext
from telegram_auto_poll.bot.wizards.store import WizardStore
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.services.chat_service import ChatService
from telegram_auto_poll.services.delivery_service import DeliveryService
from telegram_auto_poll.services.poll_service import PollService
from telegram_auto_poll.services.schedule_service import ScheduleService
from tests.conftest import ADMIN_USER_ID, Seed
from tests.factories import FIXED_NOW
from tests.fakes import FakeChatGateway, FakeClock, FakeNotifier, FakePacer, FakeSender

TOKEN = "123456789:TEST-TOKEN-DO-NOT-USE-abcdefghijklmnop"


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.answers: list[dict[str, object]] = []
        self.commands: list[dict[str, object]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> object:
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return object()

    async def answer_callback_query(self, callback_query_id: str, **kwargs: object) -> bool:
        self.answers.append({"id": callback_query_id, **kwargs})
        return True

    async def set_my_commands(self, commands: object, **kwargs: object) -> bool:
        self.commands.append({"commands": commands, **kwargs})
        return True


def build_context(
    factory: async_sessionmaker[AsyncSession], responder: object | None = None
) -> HandlerContext:
    clock = FakeClock(FIXED_NOW)
    return HandlerContext(
        guard=AdminGuard(admin_user_ids=frozenset({ADMIN_USER_ID}), clock=clock),
        chats=ChatService(
            uow=lambda: unit_of_work(factory),
            gateway=FakeChatGateway(),
            clock=clock,
            bot_user_id=1,
        ),
        polls=PollService(uow=lambda: unit_of_work(factory), clock=clock),
        schedules=ScheduleService(uow=lambda: unit_of_work(factory), clock=clock),
        deliveries=DeliveryService(
            uow=lambda: unit_of_work(factory),
            sender=FakeSender(),
            notifier=FakeNotifier(),
            pacer=FakePacer(),
            clock=clock,
        ),
        uow=lambda: unit_of_work(factory),
        wizards=WizardStore(clock=clock),
        clock=clock,
        responder=responder or SimpleNamespace(),  # type: ignore[arg-type]
        version="0.1.0",
        build_sha="abc1234",
        started_at=FIXED_NOW,
        default_timezone="UTC",
        heartbeat_age=lambda: 0.0,
    )


async def test_the_command_menu_covers_every_command() -> None:
    assert [entry.command for entry in command_menu()] == [name for name, _ in COMMAND_MENU]
    assert len(command_menu()) == 17


def test_the_command_scope_is_private_chats_only() -> None:
    assert isinstance(private_command_scope(), BotCommandScopeAllPrivateChats)


async def test_the_responder_forwards_text_and_answers_callbacks() -> None:
    bot = FakeBot()
    responder = BotResponder(bot)  # type: ignore[arg-type]

    await responder.send(1, "hello", keyboard=None)
    await responder.answer("cb-1", "done")

    assert bot.messages == [{"chat_id": 1, "text": "hello", "reply_markup": None}]
    assert bot.answers == [{"id": "cb-1", "text": "done"}]


async def test_the_application_registers_both_handlers_and_the_error_handler(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    application = build_application(token=TOKEN, ctx=build_context(factory))

    handlers = application.handlers[0]
    assert isinstance(handlers[0], MessageHandler)
    assert isinstance(handlers[1], CallbackQueryHandler)
    assert len(application.error_handlers) == 1
    assert application.post_init is _register_commands


async def test_register_commands_uses_the_private_scope() -> None:
    bot = FakeBot()

    await register_commands(bot)  # type: ignore[arg-type]

    assert len(bot.commands) == 1
    assert isinstance(bot.commands[0]["scope"], BotCommandScopeAllPrivateChats)
    assert len(bot.commands[0]["commands"]) == 17


async def test_post_init_registers_the_menu_on_the_application_bot() -> None:
    bot = FakeBot()

    await _register_commands(SimpleNamespace(bot=bot))  # type: ignore[arg-type]

    assert len(bot.commands) == 1


async def test_the_registered_callbacks_delegate(
    seed: Seed,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: object,
) -> None:
    calls: list[str] = []

    async def fake_command(update: object, ctx: object) -> None:
        calls.append("command")

    async def fake_callback(update: object, ctx: object) -> None:
        calls.append("callback")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "telegram_auto_poll.bot.application.handle_command", fake_command
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "telegram_auto_poll.bot.application.handle_callback", fake_callback
    )
    application = build_application(token=TOKEN, ctx=build_context(factory))
    message_handler, callback_handler = application.handlers[0]

    await message_handler.callback(SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]
    await callback_handler.callback(SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]
    error_callback = next(iter(application.error_handlers))
    await error_callback(SimpleNamespace(), SimpleNamespace(error=RuntimeError("boom")))

    assert calls == ["command", "callback"]


async def test_start_works_through_the_registered_handler(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    bot = FakeBot()
    ctx = build_context(factory, responder=BotResponder(bot))  # type: ignore[arg-type]
    application = build_application(token=TOKEN, ctx=ctx)
    message_handler = application.handlers[0][0]
    update = SimpleNamespace(
        message=SimpleNamespace(
            text="/start",
            chat=SimpleNamespace(id=1, type="private"),
            from_user=SimpleNamespace(id=ADMIN_USER_ID),
        )
    )

    await message_handler.callback(update, SimpleNamespace())  # type: ignore[arg-type]

    assert "telegram-auto-poll 0.1.0" in str(bot.messages[0]["text"])
