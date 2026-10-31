"""PTB application wiring (T-046, FR-109, FR-110).

The dispatcher stays deliberately thin: one private-chat message handler routes to
``handle_command`` (which performs authorization) and one callback handler routes to
``handle_callback``. Only private chats are filtered, so group traffic never reaches the bot at all.
"""

from __future__ import annotations

import logging
import typing

from telegram import Bot, BotCommand, BotCommandScopeAllPrivateChats, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .handlers.commands import HandlerContext, handle_callback, handle_command

logger = logging.getLogger(__name__)

COMMAND_MENU: tuple[tuple[str, str], ...] = (
    ("start", "Show the admin menu"),
    ("help", "List commands"),
    ("version", "Show version and build"),
    ("status", "Show health, counts, and recent failures"),
    ("chats", "List registered chats"),
    ("addchat", "Register a chat by forward or id"),
    ("removechat", "Deactivate a chat"),
    ("polls", "List polls"),
    ("poll", "Show a poll"),
    ("newpoll", "Create a poll"),
    ("schedules", "List schedules"),
    ("schedule", "Show a schedule"),
    ("pause", "Pause a schedule"),
    ("resume", "Resume a schedule"),
    ("delete_schedule", "Delete a schedule"),
    ("sendnow", "Send a poll now"),
    ("cancel", "Cancel the open wizard"),
)


def command_menu() -> list[BotCommand]:
    """Return the private-chat command menu."""
    return [BotCommand(command=name, description=description) for name, description in COMMAND_MENU]


def private_command_scope() -> BotCommandScopeAllPrivateChats:
    """Return the only scope the bot registers commands in (FR-110)."""
    return BotCommandScopeAllPrivateChats()


class BotResponder:
    """``Responder`` backed by ``Bot.send_message`` and ``Bot.answer_callback_query``."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(
        self, chat_id: int, text: str, *, keyboard: InlineKeyboardMarkup | None = None
    ) -> None:
        await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)

    async def answer(self, callback_query_id: str, text: str) -> None:
        await self._bot.answer_callback_query(callback_query_id=callback_query_id, text=text)


#: PTB parameterises ``Application`` with six type variables; the bot does not need them.
ApplicationType = Application[
    typing.Any, typing.Any, typing.Any, typing.Any, typing.Any, typing.Any
]


def build_application(*, token: str, ctx: HandlerContext) -> ApplicationType:
    """Build the PTB application with both handlers and the private command menu."""

    application = Application.builder().token(token).build()

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await handle_command(update, ctx)

    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await handle_callback(update, ctx)

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error(
            "telegram update failed",
            extra={"error": str(context.error)},
            exc_info=context.error,
        )

    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_message))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_error_handler(on_error)
    application.post_init = _register_commands
    return application


async def register_commands(bot: Bot) -> None:
    """Register the command menu for private chats only."""
    await bot.set_my_commands(command_menu(), scope=private_command_scope())


async def _register_commands(application: ApplicationType) -> None:
    await register_commands(application.bot)
