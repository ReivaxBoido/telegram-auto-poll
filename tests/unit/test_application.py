"""PTB application wiring (SPEC-003 FR-341)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from telegram import InlineKeyboardMarkup

from telegram_auto_poll.bot.application import (
    BotResponder,
    build_application,
    command_menu,
    private_command_scope,
    register_commands,
)
from tests.fakes import FakeBot, FakeClock


def test_command_menu_and_scope() -> None:
    menu = command_menu()
    names = [command.command for command in menu]
    assert "newpoll" in names
    assert "publish" in names
    assert "deletepoll" in names
    assert "sendnow" not in names
    assert private_command_scope() is not None


async def test_register_commands_uses_private_scope() -> None:
    bot = FakeBot()
    await register_commands(bot)  # type: ignore[arg-type]
    assert bot.commands[0]["scope"] == private_command_scope()


async def test_bot_responder_sends_edits_and_answers() -> None:
    bot = FakeBot()
    responder = BotResponder(bot)  # type: ignore[arg-type]
    keyboard = InlineKeyboardMarkup([])

    await responder.send(1, "hello", keyboard=keyboard)
    await responder.edit(1, 2, "edited", keyboard=keyboard)
    await responder.answer("callback", "ok")

    assert bot.messages[0]["text"] == "hello"
    assert bot.edits[0]["text"] == "edited"
    assert bot.callback_answers[0]["text"] == "ok"


def test_build_application_registers_both_handlers() -> None:
    ctx = SimpleNamespace()
    application = build_application(token="123:abc", ctx=ctx)  # type: ignore[arg-type]
    assert len(application.handlers[0]) == 2


async def test_build_application_handlers_route_updates(monkeypatch: Any) -> None:
    from telegram_auto_poll.bot.auth import AdminGuard

    class Recorder:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.answers: list[str] = []

        async def send(self, chat_id: int, text: str, *, keyboard: object = None) -> None:
            self.sent.append(text)

        async def answer(self, callback_query_id: str, text: str) -> None:
            self.answers.append(text)

    responder = Recorder()
    ctx = SimpleNamespace(
        guard=AdminGuard(
            admin_user_ids=frozenset(),
            clock=FakeClock(),
        ),
        responder=responder,
        wizards=SimpleNamespace(get=lambda _: None),
    )
    application = build_application(token="123:abc", ctx=ctx)  # type: ignore[arg-type]

    message = SimpleNamespace(
        message=SimpleNamespace(
            chat=SimpleNamespace(id=1, type="private"),
            from_user=SimpleNamespace(id=2),
            text="/start",
        )
    )
    await application.handlers[0][0].callback(message, None)  # type: ignore[union-attr]
    assert responder.sent[0].startswith("This is a private bot")

    callback = SimpleNamespace(
        callback_query=SimpleNamespace(
            id="cb",
            from_user=SimpleNamespace(id=2),
            data="bad",
            message=None,
            inline_message_id=None,
        )
    )
    await application.handlers[0][1].callback(callback, None)  # type: ignore[union-attr]
    assert responder.answers == ["invalid action"]

    error_handler = next(iter(application.error_handlers))
    await error_handler(None, SimpleNamespace(error=RuntimeError("x")))  # type: ignore[arg-type]


async def test_register_commands_post_init(monkeypatch: Any) -> None:
    from telegram_auto_poll.bot import application as application_module

    called: list[object] = []

    async def fake_register(bot: object) -> None:
        called.append(bot)

    monkeypatch.setattr(application_module, "register_commands", fake_register)
    application = build_application(token="123:abc", ctx=SimpleNamespace())  # type: ignore[arg-type]
    await application.post_init(application)  # type: ignore[misc]
    assert called == [application.bot]
