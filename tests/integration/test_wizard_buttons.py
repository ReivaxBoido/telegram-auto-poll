"""Button-only wizard flow against real SQLite (T-211, FR-202 - FR-211)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.bot.callbacks import wizard_action
from telegram_auto_poll.db.uow import unit_of_work
from telegram_auto_poll.domain.enums import ScheduleKind
from tests.conftest import Seed
from tests.unit.test_handlers import build, callback, message


async def test_the_whole_wizard_can_be_driven_by_buttons(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx, responder, _, _ = build(factory)
    from telegram_auto_poll.bot.handlers.commands import handle_callback, handle_command

    await handle_command(message("/newpoll"), ctx)
    await handle_command(message("Buttons only?"), ctx)
    await handle_callback(callback(wizard_action("skip")), ctx)
    await handle_command(message("Yes"), ctx)
    await handle_command(message("No"), ctx)
    await handle_callback(callback(wizard_action("done")), ctx)
    await handle_callback(callback(wizard_action("next")), ctx)  # anonymity
    await handle_callback(callback(wizard_action("next")), ctx)  # multiple
    await handle_callback(callback(wizard_action("tgt", seed.chat_id)), ctx)
    await handle_callback(callback(wizard_action("done")), ctx)
    await handle_callback(callback(wizard_action("kind", "monthly")), ctx)
    await handle_command(message("15"), ctx)
    await handle_command(message("09:00"), ctx)
    await handle_callback(callback(wizard_action("skip")), ctx)  # timezone
    await handle_callback(callback(wizard_action("skip")), ctx)  # start
    await handle_callback(callback(wizard_action("skip")), ctx)  # end
    await handle_callback(callback(wizard_action("create")), ctx)

    assert "created poll #" in responder.last
    async with unit_of_work(factory) as repos:
        poll = await repos.polls.get(2)
        schedules = await repos.schedules.list_all(poll_id=2)
    assert poll is not None
    assert poll.options == ("Yes", "No")
    assert schedules[0].kind is ScheduleKind.MONTHLY
    assert schedules[0].day_of_month == 15


async def test_target_buttons_show_names_and_page(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    from telegram_auto_poll.bot.handlers.commands import handle_callback, handle_command
    from telegram_auto_poll.bot.wizards.states import WizardState, WizardStep
    from tests.factories import make_chat

    ctx, responder, _, _ = build(factory)
    async with unit_of_work(factory) as repos:
        for index in range(8):
            await repos.chats.upsert(
                make_chat(id=None, telegram_chat_id=-2_000_000_000_000 - index),
                now=__import__("tests.factories", fromlist=["FIXED_NOW"]).FIXED_NOW,
            )
    ctx.wizards.put(WizardState(admin_user_id=100_000_001, step=WizardStep.TARGETS))
    await handle_command(message("hello"), ctx)
    keyboard = responder.sent[-1][2]
    assert keyboard is not None
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert any("Ops" in label or "Automation" in label for label in labels)

    nav = keyboard.inline_keyboard[6][0]
    await handle_callback(callback(str(nav.callback_data)), ctx)
    assert ctx.wizards.get(100_000_001).chat_page == 2  # type: ignore[union-attr]
