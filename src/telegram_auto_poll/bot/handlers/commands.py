"""Admin commands and callbacks for CountMeIn-style polls (SPEC-003 §8).

Handlers are plain coroutines taking ``(update, context)`` and replying through the ``Responder``
port. That keeps every command directly invocable in tests with a fake update and a recording
responder.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from telegram import InlineKeyboardMarkup

from ...db.uow import UnitOfWork
from ...domain.callbacks import CallbackDataError, decode, encode
from ...domain.entities import Chat, Poll
from ...domain.enums import VoteAction
from ...domain.errors import DomainError, PollClosedError, ResponseRejectedError, ValidationError
from ...domain.ports import Clock
from ...services.chat_service import ChatService, parse_chat_id
from ...services.poll_service import PollService
from ...services.posting_worker import PostingWorker
from ...services.publish_service import PublishService
from ...services.response_service import ResponseService
from ...services.status_service import StatusService
from ..auth import (
    GENERIC_REFUSAL,
    AdminGuard,
    AuthDenial,
    command_name,
    rate_limit_message,
    vote_rate_limit_message,
)
from ..keyboards import (
    build_keyboard,
    confirmation_keyboard,
    pagination_keyboard,
    poll_detail_keyboard,
    target_keyboard,
)
from ..publish_store import PublishSession, PublishStore
from ..render import (
    chat_line,
    empty_listing,
    page_hint,
    poll_detail,
    poll_line,
    publish_confirmation,
    response_page,
    status_message,
    target_selection,
)
from ..wizards.engine import PollDraft, Transition, WizardEffect, advance, remove_option
from ..wizards.states import WizardEvent, WizardInput, WizardState, WizardStep
from ..wizards.store import WizardStore

logger = logging.getLogger(__name__)

HELP_TEXT = "\n".join(
    [
        "Commands:",
        "/start /help /version /status",
        "/chats [page] /addchat /removechat <id>",
        "/polls [page] /poll <id> /newpoll",
        "/publish <id> /close <id> /reopen <id>",
        "/refresh <id> /responses <id> [option] [page]",
        "/deletepoll <id> /cancel",
    ]
)

TARGET_PAGE_SIZE = 6


class Responder(Protocol):
    """Sends, edits, and answers through Telegram."""

    async def send(
        self, chat_id: int, text: str, *, keyboard: InlineKeyboardMarkup | None = None
    ) -> None:
        raise NotImplementedError

    async def edit(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> None:
        raise NotImplementedError

    async def answer(self, callback_query_id: str, text: str) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class HandlerContext:
    """Everything a handler may use."""

    guard: AdminGuard
    chats: ChatService
    polls: PollService
    publish: PublishService
    responses: ResponseService
    status: StatusService
    uow: UnitOfWork
    wizards: WizardStore
    publish_sessions: PublishStore
    clock: Clock
    responder: Responder
    posting: PostingWorker
    version: str
    build_sha: str
    started_at: datetime
    heartbeat_age: Callable[[], float | None]
    db_check: Callable[[], Awaitable[bool]]


def chat_id_of(update: object) -> int:
    """Return the chat id of a private message update."""
    message = getattr(update, "message", None)
    if message is None:
        raise ValueError("update has no message")
    return int(message.chat.id)


def user_id_of(update: object) -> int:
    """Return the sender's user id, or ``0`` for a message without a sender."""
    message = getattr(update, "message", None)
    user = getattr(message, "from_user", None)
    return int(getattr(user, "id", 0))


def message_text(update: object) -> str | None:
    """Return the message text when present."""
    text = getattr(getattr(update, "message", None), "text", None)
    return text if isinstance(text, str) else None


def argument(update: object) -> str | None:
    """Return the first argument after the command, if any."""
    text = message_text(update)
    if text is None:
        return None
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 and parts[1].strip() else None


def parse_int(value: str | None, *, field: str, minimum: int = 1) -> int:
    """Parse a required positive integer argument."""
    if value is None or not value.lstrip("-").isdigit():
        raise ValidationError(field, f"send a numeric {field}")
    number = int(value)
    if number < minimum:
        raise ValidationError(field, f"{field} must be at least {minimum}")
    return number


async def _admit(update: object, ctx: HandlerContext, command: str | None) -> int | None:
    """Authorize and charge one command; reply on refusal."""
    decision = ctx.guard.authorize_message(update)
    if not decision.allowed:
        if decision.reason is AuthDenial.NOT_ADMIN:
            await ctx.responder.send(chat_id_of(update), GENERIC_REFUSAL)
        return None
    charged = ctx.guard.consume_command(user_id_of(update), command)
    if not charged.allowed:
        await ctx.responder.send(chat_id_of(update), rate_limit_message(charged.retry_after or 60))
        return None
    return chat_id_of(update)


async def _reply_error(chat_id: int, ctx: HandlerContext, error: DomainError) -> None:
    await ctx.responder.send(chat_id, str(error))


# --------------------------------------------------------------------------------------------
# Basic commands
# --------------------------------------------------------------------------------------------


async def start(update: object, ctx: HandlerContext) -> None:
    """/start: identify the admin and show the short menu."""
    chat_id = await _admit(update, ctx, "start")
    if chat_id is None:
        return
    await ctx.responder.send(
        chat_id,
        f"telegram-auto-poll {ctx.version}\nadmin id: {user_id_of(update)}\n\n{HELP_TEXT}",
    )


async def help_command(update: object, ctx: HandlerContext) -> None:
    """/help: list the commands."""
    chat_id = await _admit(update, ctx, "help")
    if chat_id is None:
        return
    await ctx.responder.send(chat_id, HELP_TEXT)


async def version(update: object, ctx: HandlerContext) -> None:
    """/version: version, build SHA, and start time."""
    chat_id = await _admit(update, ctx, "version")
    if chat_id is None:
        return
    await ctx.responder.send(
        chat_id,
        f"version: {ctx.version}\nbuild: {ctx.build_sha}\nstarted: {ctx.started_at.isoformat()}",
    )


async def status(update: object, ctx: HandlerContext) -> None:
    """/status: health, counts, and recent failures (FR-338)."""
    chat_id = await _admit(update, ctx, "status")
    if chat_id is None:
        return
    now = ctx.clock.now()
    snapshot = await ctx.status.snapshot()
    db_ok = await ctx.db_check()
    await ctx.responder.send(
        chat_id,
        status_message(
            version=ctx.version,
            build_sha=ctx.build_sha,
            uptime_seconds=(now - ctx.started_at).total_seconds(),
            db_ok=db_ok,
            heartbeat_age=ctx.heartbeat_age(),
            snapshot=snapshot,
        ),
    )


async def cancel(update: object, ctx: HandlerContext) -> None:
    """/cancel: drop the admin's wizard and publish session."""
    chat_id = await _admit(update, ctx, "cancel")
    if chat_id is None:
        return
    admin = user_id_of(update)
    had_wizard = ctx.wizards.get(admin) is not None
    had_publish = ctx.publish_sessions.get(admin) is not None
    ctx.wizards.discard(admin)
    ctx.publish_sessions.discard(admin)
    await ctx.responder.send(
        chat_id,
        "cancelled" if had_wizard or had_publish else "there is nothing to cancel",
    )


# --------------------------------------------------------------------------------------------
# Chats
# --------------------------------------------------------------------------------------------


async def chats(update: object, ctx: HandlerContext) -> None:
    """/chats [page]: paginated registered chats."""
    chat_id = await _admit(update, ctx, "chats")
    if chat_id is None:
        return
    try:
        page_number = parse_int(argument(update), field="page") if argument(update) else 1
        page = await ctx.chats.page(page_number)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    if not page.items:
        await ctx.responder.send(chat_id, empty_listing("/addchat"))
        return
    keyboard = pagination_keyboard(
        namespace="w",
        action="chats",
        page=page.number,
        has_previous=page.has_previous,
        has_next=page.has_next,
    )
    body = "\n".join(chat_line(chat) for chat in page.items)
    await ctx.responder.send(chat_id, f"{body}\n\n{page_hint(page)}", keyboard=keyboard)


async def addchat(update: object, ctx: HandlerContext) -> None:
    """/addchat: register by forwarded message or pasted id."""
    chat_id = await _admit(update, ctx, "addchat")
    if chat_id is None:
        return
    from ..telegram_gateway import forwarded_chat_id

    forwarded = forwarded_chat_id(getattr(update, "message", None))
    try:
        telegram_id = forwarded if forwarded is not None else parse_chat_id(argument(update) or "")
        result = await ctx.chats.register(telegram_id, actor_user_id=user_id_of(update))
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    lines = [f"registered #{result.chat.id} {result.chat.title} ({result.chat.type})"]
    if result.reactivated:
        lines.append("reactivated a previously removed chat")
    if result.warning:
        lines.append(result.warning)
    await ctx.responder.send(chat_id, "\n".join(lines))


async def removechat(update: object, ctx: HandlerContext) -> None:
    """/removechat <id>: show a confirmation; the mutation happens on the callback."""
    chat_id = await _admit(update, ctx, "removechat")
    if chat_id is None:
        return
    try:
        target = parse_int(argument(update), field="chat_id")
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    async with ctx.uow() as repos:
        chat = await repos.chats.get(target)
    if chat is None:
        await ctx.responder.send(chat_id, f"chat {target} is not registered")
        return
    await ctx.responder.send(
        chat_id,
        f"deactivate #{chat.id} {chat.title}? pending messages become skipped.",
        keyboard=confirmation_keyboard(
            entity="chat",
            entity_id=chat.id or target,
            confirm_action="removechat",
        ),
    )


# --------------------------------------------------------------------------------------------
# Polls
# --------------------------------------------------------------------------------------------


async def polls(update: object, ctx: HandlerContext) -> None:
    """/polls [page]: paginated poll summaries."""
    chat_id = await _admit(update, ctx, "polls")
    if chat_id is None:
        return
    try:
        page = await ctx.polls.page(
            parse_int(argument(update), field="page") if argument(update) else 1
        )
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    if not page.items:
        await ctx.responder.send(chat_id, empty_listing("/newpoll"))
        return
    keyboard = pagination_keyboard(
        namespace="w",
        action="polls",
        page=page.number,
        has_previous=page.has_previous,
        has_next=page.has_next,
    )
    body = "\n".join(poll_line(summary) for summary in page.items)
    await ctx.responder.send(chat_id, f"{body}\n\n{page_hint(page)}", keyboard=keyboard)


async def poll(update: object, ctx: HandlerContext) -> None:
    """/poll <id>: definition, counts, latest instance, and actions."""
    chat_id = await _admit(update, ctx, "poll")
    if chat_id is None:
        return
    try:
        poll_id = parse_int(argument(update), field="poll_id")
        detail = await ctx.polls.detail(poll_id)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    await ctx.responder.send(
        chat_id,
        poll_detail(detail),
        keyboard=poll_detail_keyboard(detail.poll, detail.latest_instance),
    )


async def close(update: object, ctx: HandlerContext) -> None:
    """/close <id>: show a confirmation; the mutation happens on the callback."""
    await _confirm_poll_action(update, ctx, "close")


async def reopen(update: object, ctx: HandlerContext) -> None:
    """/reopen <id>: show a confirmation."""
    await _confirm_poll_action(update, ctx, "reopen")


async def deletepoll(update: object, ctx: HandlerContext) -> None:
    """/deletepoll <id>: show a destructive confirmation."""
    await _confirm_poll_action(update, ctx, "delete")


async def _confirm_poll_action(update: object, ctx: HandlerContext, action: str) -> None:
    chat_id = await _admit(update, ctx, action)
    if chat_id is None:
        return
    try:
        poll_id = parse_int(argument(update), field="poll_id")
        detail = await ctx.polls.detail(poll_id)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    labels = {
        "close": "close the latest open instance",
        "reopen": "reopen the latest closed instance",
        "delete": "delete the poll and remove every response",
    }
    await ctx.responder.send(
        chat_id,
        f'{labels[action]} for poll #{poll_id} "{detail.poll.title}"?',
        keyboard=confirmation_keyboard(
            entity="poll",
            entity_id=poll_id,
            confirm_action=action,
        ),
    )


async def refresh(update: object, ctx: HandlerContext) -> None:
    """/refresh <id>: retry failed messages and re-render the latest instance."""
    chat_id = await _admit(update, ctx, "refresh")
    if chat_id is None:
        return
    try:
        poll_id = parse_int(argument(update), field="poll_id")
        result = await ctx.polls.refresh(poll_id, actor_user_id=user_id_of(update))
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    ctx.posting.wake()
    await ctx.responder.send(
        chat_id,
        f"refreshed instance #{result.instance_id}: retried {result.retried}, "
        f"edited {result.broadcast.edited}, missing {result.broadcast.missing}, "
        f"failed {result.broadcast.failed}",
    )


async def responses(update: object, ctx: HandlerContext) -> None:
    """/responses <id> [option] [page]: names for one option, paginated."""
    chat_id = await _admit(update, ctx, "responses")
    if chat_id is None:
        return
    raw = argument(update) or ""
    parts = raw.split()
    try:
        poll_id = parse_int(parts[0] if parts else None, field="poll_id")
        option = parse_int(parts[1], field="option") if len(parts) > 1 else 1
        page_number = parse_int(parts[2], field="page") if len(parts) > 2 else 1
        page = await ctx.polls.responses(poll_id, option_position=option, page=page_number)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    keyboard = _response_keyboard(poll_id, option, page.number, page.pages)
    await ctx.responder.send(chat_id, response_page(page), keyboard=keyboard)


def _response_keyboard(
    poll_id: int, option: int, page: int, pages: int
) -> InlineKeyboardMarkup | None:
    rows: list[list[tuple[str, str]]] = []
    navigation: list[tuple[str, str]] = []
    if page > 1:
        navigation.append(("Previous", encode("p", "resp", f"{poll_id}_{option}_{page - 1}")))
    if page < pages:
        navigation.append(("Next", encode("p", "resp", f"{poll_id}_{option}_{page + 1}")))
    if navigation:
        rows.append(navigation)
    return build_keyboard(rows) if rows else None


# --------------------------------------------------------------------------------------------
# New poll wizard
# --------------------------------------------------------------------------------------------


async def newpoll(update: object, ctx: HandlerContext) -> None:
    """/newpoll: start a wizard session and ask for the title."""
    chat_id = await _admit(update, ctx, "newpoll")
    if chat_id is None:
        return
    admin = user_id_of(update)
    if ctx.wizards.get(admin) is not None:
        await ctx.responder.send(
            chat_id, "a wizard is already open. Finish it, or send /cancel and start again."
        )
        return
    ctx.wizards.put(WizardState(admin_user_id=admin))
    await ctx.responder.send(chat_id, _render_step(WizardState(admin_user_id=admin)))


def _wizard_input(state: WizardState, text: str) -> WizardInput:
    lowered = text.strip().lower()
    if lowered == "back":
        return WizardInput(WizardEvent.BACK)
    if lowered == "cancel":
        return WizardInput(WizardEvent.CANCEL)
    if lowered == "done":
        return WizardInput(WizardEvent.OPTION_DONE)
    if lowered == "confirm":
        return WizardInput(WizardEvent.CONFIRM)
    return WizardInput(WizardEvent.TEXT, text=text)


def _render_step(state: WizardState) -> str:
    if state.step is WizardStep.TITLE:
        return _prompt("Send the poll title (1-300 characters).")
    if state.step is WizardStep.OPTIONS:
        listed = (
            "\n".join(f"  {index}. {option}" for index, option in enumerate(state.options, 1))
            or "  (none yet)"
        )
        return _prompt(
            f"Title: {state.title}\nOptions:\n{listed}\n\n"
            "Send the next option, or send 'done' to preview."
        )
    if state.step is WizardStep.PREVIEW:
        listed = "\n".join(f"  {index}. {option}" for index, option in enumerate(state.options, 1))
        return _prompt(
            "Preview\n"
            f"title: {state.title}\n"
            f"options:\n{listed}\n\n"
            "Press Confirm to create the poll, or Back to change something."
        )
    if state.step is WizardStep.CANCELLED:
        return "wizard cancelled"
    return "wizard complete"


def _prompt(text: str) -> str:
    return f"{text}\n\n(back = previous step, /cancel = abort)"


def _wizard_keyboard(state: WizardState) -> InlineKeyboardMarkup | None:
    if state.step is WizardStep.OPTIONS:
        rows: list[list[tuple[str, str]]] = []
        if state.options:
            rows.append(
                [
                    ("Remove last", encode("w", "option_remove", len(state.options) - 1)),
                    ("Done", encode("w", "option_done", None)),
                ]
            )
        rows.append([("Cancel", encode("w", "cancel", None))])
        return build_keyboard(rows)
    if state.step is WizardStep.PREVIEW:
        return build_keyboard(
            [
                [
                    ("Confirm", encode("w", "confirm", None)),
                    ("Back", encode("w", "back", None)),
                ],
                [("Cancel", encode("w", "cancel", None))],
            ]
        )
    return None


async def handle_wizard_message(update: object, ctx: HandlerContext) -> None:
    """Feed one plain-text message to the admin's wizard session."""
    chat_id = await _admit(update, ctx, None)
    if chat_id is None:
        return
    admin = user_id_of(update)
    pending = ctx.wizards.get(admin)
    if pending is None:
        await ctx.responder.send(chat_id, "no wizard is open. Use /newpoll to start one.")
        return
    transition = advance(pending, _wizard_input(pending, message_text(update) or ""))
    await _apply_wizard_transition(chat_id, admin, transition, ctx)


async def _apply_wizard_transition(
    chat_id: int,
    admin: int,
    transition: Transition,
    ctx: HandlerContext,
) -> None:
    if transition.effect is WizardEffect.COMMIT and transition.draft is not None:
        await _create_from_draft(chat_id, admin, transition.draft, ctx)
        return
    if transition.effect is WizardEffect.CANCELLED:
        ctx.wizards.discard(admin)
        await ctx.responder.send(chat_id, "wizard cancelled")
        return
    ctx.wizards.put(transition.state)
    if transition.effect is WizardEffect.IGNORED:
        await ctx.responder.send(chat_id, "this wizard is already finished")
        return
    prompt = _render_step(transition.state)
    if transition.state.error:
        prompt = f"{transition.state.error}\n\n{prompt}"
    await ctx.responder.send(chat_id, prompt, keyboard=_wizard_keyboard(transition.state))


async def _create_from_draft(
    chat_id: int,
    admin: int,
    draft: PollDraft,
    ctx: HandlerContext,
) -> None:
    try:
        created = await ctx.polls.create(
            actor_user_id=admin,
            title=draft.title,
            options=draft.options,
        )
    except DomainError as error:
        await ctx.responder.send(chat_id, f"not created: {error}")
        return
    ctx.wizards.discard(admin)
    detail = await ctx.polls.detail(created.id or 0)
    await ctx.responder.send(
        chat_id,
        f"created poll #{created.id}\n\n{poll_detail(detail)}",
        keyboard=poll_detail_keyboard(detail.poll, detail.latest_instance),
    )


# --------------------------------------------------------------------------------------------
# Publish target selection
# --------------------------------------------------------------------------------------------


async def publish(update: object, ctx: HandlerContext) -> None:
    """/publish <id>: start the target selection."""
    chat_id = await _admit(update, ctx, "publish")
    if chat_id is None:
        return
    try:
        poll_id = parse_int(argument(update), field="poll_id")
        detail = await ctx.polls.detail(poll_id)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    session = PublishSession(admin_user_id=user_id_of(update), poll_id=poll_id)
    ctx.publish_sessions.put(session)
    await _show_targets(chat_id, detail.poll, session, ctx)


async def _show_targets(
    chat_id: int,
    poll: Poll,
    session: PublishSession,
    ctx: HandlerContext,
    *,
    message_id: int | None = None,
) -> None:
    page = await ctx.chats.page_sized(session.page, size=TARGET_PAGE_SIZE, active_only=True)
    text = target_selection(
        poll,
        selected=session.selected,
        page=page.number,
        pages=page.pages,
    )
    keyboard = target_keyboard(
        poll_id=poll.id or 0,
        chats=page.items,
        selected=session.selected,
        page=page.number,
        has_previous=page.has_previous,
        has_next=page.has_next,
    )
    if message_id is not None:
        await ctx.responder.edit(chat_id, message_id, text, keyboard=keyboard)
    else:
        await ctx.responder.send(chat_id, text, keyboard=keyboard)


# --------------------------------------------------------------------------------------------
# Callback routing
# --------------------------------------------------------------------------------------------


def _callback_query(update: object) -> object:
    query: object = update.callback_query  # type: ignore[attr-defined]
    return query


def _callback_chat_id(update: object) -> int:
    message = getattr(_callback_query(update), "message", None)
    chat = getattr(message, "chat", None)
    return int(getattr(chat, "id", 0))


def _callback_message_id(update: object) -> int | None:
    message = getattr(_callback_query(update), "message", None)
    value = getattr(message, "message_id", None)
    return int(value) if value is not None else None


def _callback_actor(update: object) -> int:
    return int(getattr(getattr(_callback_query(update), "from_user", None), "id", 0))


def _callback_first_name(update: object) -> str | None:
    value = getattr(getattr(_callback_query(update), "from_user", None), "first_name", None)
    return str(value) if value is not None else None


def _callback_inline_message_id(update: object) -> str | None:
    value = getattr(_callback_query(update), "inline_message_id", None)
    return str(value) if value is not None else None


async def handle_callback(update: object, ctx: HandlerContext) -> None:
    """Route one callback: participant vote or admin action."""
    query = _callback_query(update)
    callback_id = str(getattr(query, "id", ""))
    try:
        data = decode(str(getattr(query, "data", "")))
    except CallbackDataError:
        await ctx.responder.answer(callback_id, "invalid action")
        return

    if data.namespace == "v":
        await _handle_vote(update, callback_id, data.argument, ctx)
        return

    decision = ctx.guard.authorize_admin_callback(update)
    if not decision.allowed:
        await ctx.responder.answer(callback_id, "not allowed")
        return

    chat_id = _callback_chat_id(update)
    if data.namespace == "w":
        await _handle_wizard_callback(update, chat_id, callback_id, data.action, data.argument, ctx)
    elif data.namespace == "t":
        await _handle_target_callback(update, chat_id, callback_id, data.action, data.argument, ctx)
    elif data.namespace == "p":
        await _handle_poll_callback(update, chat_id, callback_id, data.action, data.argument, ctx)
    else:
        await _handle_confirm_callback(
            update, chat_id, callback_id, data.action, data.argument, ctx
        )


async def _handle_vote(
    update: object,
    callback_id: str,
    argument_value: str | None,
    ctx: HandlerContext,
) -> None:
    decision = ctx.guard.authorize_vote_callback(update)
    if not decision.allowed:
        await ctx.responder.answer(callback_id, "not allowed")
        return
    pair = _parse_pair(argument_value)
    if pair is None:
        await ctx.responder.answer(callback_id, "invalid action")
        return
    instance_id, option_id = pair
    charged = ctx.guard.consume_vote(_callback_actor(update))
    if not charged.allowed:
        await ctx.responder.answer(callback_id, vote_rate_limit_message(charged.retry_after or 60))
        return
    try:
        result = await ctx.responses.toggle(
            instance_id=instance_id,
            option_id=option_id,
            telegram_user_id=_callback_actor(update),
            first_name=_callback_first_name(update),
            chat_id=_callback_chat_id(update) or None,
            message_id=_callback_message_id(update),
            inline_message_id=_callback_inline_message_id(update),
        )
    except PollClosedError:
        await ctx.responder.answer(callback_id, "Voting is closed")
        return
    except ResponseRejectedError as error:
        await ctx.responder.answer(callback_id, str(error))
        return
    verb = "Added to" if result.action is VoteAction.ADDED else "Removed from"
    await ctx.responder.answer(callback_id, f"{verb} {result.option_label}")


async def _handle_wizard_callback(
    update: object,
    chat_id: int,
    callback_id: str,
    action: str,
    argument_value: str | None,
    ctx: HandlerContext,
) -> None:
    admin = _callback_actor(update)
    if action == "cancel":
        ctx.wizards.discard(admin)
        await ctx.responder.answer(callback_id, "cancelled")
        return
    pending = ctx.wizards.get(admin)
    if pending is None:
        await ctx.responder.answer(callback_id, "the wizard expired; use /newpoll")
        return
    if action == "option_remove":
        try:
            index = int(argument_value or -1)
        except ValueError:
            await ctx.responder.answer(callback_id, "invalid action")
            return
        updated = remove_option(pending, index)
        ctx.wizards.put(updated)
        await ctx.responder.answer(callback_id, "removed" if not updated.error else updated.error)
        await ctx.responder.send(chat_id, _render_step(updated), keyboard=_wizard_keyboard(updated))
        return
    if action == "option_done":
        transition = advance(pending, WizardInput(WizardEvent.OPTION_DONE))
        await _apply_wizard_transition(chat_id, admin, transition, ctx)
        await ctx.responder.answer(callback_id, "done")
        return
    if action == "back":
        transition = advance(pending, WizardInput(WizardEvent.BACK))
        await _apply_wizard_transition(chat_id, admin, transition, ctx)
        await ctx.responder.answer(callback_id, "back")
        return
    if action == "confirm":
        transition = advance(pending, WizardInput(WizardEvent.CONFIRM))
        await _apply_wizard_transition(chat_id, admin, transition, ctx)
        await ctx.responder.answer(callback_id, "created")
        return
    await ctx.responder.answer(callback_id, "unsupported action")


async def _handle_target_callback(
    update: object,
    chat_id: int,
    callback_id: str,
    action: str,
    argument_value: str | None,
    ctx: HandlerContext,
) -> None:
    admin = _callback_actor(update)
    session = ctx.publish_sessions.get(admin)
    if session is None:
        await ctx.responder.answer(callback_id, "start with /publish")
        return
    poll_id = session.poll_id
    message_id = _callback_message_id(update)
    if action == "cancel":
        ctx.publish_sessions.discard(admin)
        if message_id is not None:
            await ctx.responder.edit(chat_id, message_id, "publish cancelled")
        await ctx.responder.answer(callback_id, "cancelled")
        return
    if action == "page":
        parsed = _parse_pair(argument_value)
        if parsed is None or parsed[0] != poll_id:
            await ctx.responder.answer(callback_id, "invalid action")
            return
        updated = replace(session, page=max(parsed[1], 1))
        ctx.publish_sessions.put(updated)
        detail = await ctx.polls.detail(poll_id)
        await _show_targets(chat_id, detail.poll, updated, ctx, message_id=message_id)
        await ctx.responder.answer(callback_id, f"page {updated.page}")
        return
    if action == "toggle":
        parsed = _parse_pair(argument_value)
        if parsed is None or parsed[0] != poll_id:
            await ctx.responder.answer(callback_id, "invalid action")
            return
        selected = set(session.selected)
        selected.symmetric_difference_update({parsed[1]})
        updated = replace(session, selected=frozenset(selected))
        ctx.publish_sessions.put(updated)
        detail = await ctx.polls.detail(poll_id)
        await _show_targets(chat_id, detail.poll, updated, ctx, message_id=message_id)
        await ctx.responder.answer(callback_id, "selected" if parsed[1] in selected else "cleared")
        return
    if action == "done":
        if not session.selected:
            await ctx.responder.answer(callback_id, "select at least one chat")
            return
        detail = await ctx.polls.detail(poll_id)
        selected_chats = await _chats_by_id(ctx, session.selected)
        await ctx.responder.send(
            chat_id,
            publish_confirmation(detail.poll, selected_chats),
            keyboard=confirmation_keyboard(
                entity="publish",
                entity_id=poll_id,
                confirm_action="publish",
            ),
        )
        await ctx.responder.answer(callback_id, "confirm to publish")
        return
    await ctx.responder.answer(callback_id, "unsupported action")


async def _handle_poll_callback(
    update: object,
    chat_id: int,
    callback_id: str,
    action: str,
    argument_value: str | None,
    ctx: HandlerContext,
) -> None:
    if action == "resp":
        parsed = _parse_triple(argument_value)
        if parsed is not None:
            poll_id, option, page_number = parsed
        elif argument_value is not None and argument_value.isdigit():
            poll_id, option, page_number = int(argument_value), 1, 1
        else:
            await ctx.responder.answer(callback_id, "invalid action")
            return
        page = await ctx.polls.responses(poll_id, option_position=option, page=page_number)
        await ctx.responder.send(
            chat_id,
            response_page(page),
            keyboard=_response_keyboard(poll_id, option, page.number, page.pages),
        )
        await ctx.responder.answer(callback_id, "responses")
        return
    if argument_value is None or not argument_value.isdigit():
        await ctx.responder.answer(callback_id, "invalid action")
        return
    poll_id = int(argument_value)
    if action == "pub":
        detail = await ctx.polls.detail(poll_id)
        session = PublishSession(admin_user_id=_callback_actor(update), poll_id=poll_id)
        ctx.publish_sessions.put(session)
        await _show_targets(chat_id, detail.poll, session, ctx)
        await ctx.responder.answer(callback_id, "choose chats")
        return
    if action in {"close", "reopen", "delete"}:
        detail = await ctx.polls.detail(poll_id)
        labels = {
            "close": "close the latest open instance",
            "reopen": "reopen the latest closed instance",
            "delete": "delete the poll and remove every response",
        }
        await ctx.responder.send(
            chat_id,
            f'{labels[action]} for poll #{poll_id} "{detail.poll.title}"?',
            keyboard=confirmation_keyboard(
                entity="poll",
                entity_id=poll_id,
                confirm_action=action,
            ),
        )
        await ctx.responder.answer(callback_id, "confirm")
        return
    if action == "ref":
        result = await ctx.polls.refresh(poll_id, actor_user_id=_callback_actor(update))
        ctx.posting.wake()
        await ctx.responder.answer(callback_id, "refreshed")
        await ctx.responder.send(
            chat_id,
            f"refreshed instance #{result.instance_id}: retried {result.retried}, "
            f"edited {result.broadcast.edited}, missing {result.broadcast.missing}",
        )
        return
    await ctx.responder.answer(callback_id, "unsupported action")


async def _handle_confirm_callback(
    update: object,
    chat_id: int,
    callback_id: str,
    action: str,
    argument_value: str | None,
    ctx: HandlerContext,
) -> None:
    if argument_value is None or not argument_value.isdigit():
        await ctx.responder.answer(callback_id, "invalid action")
        return
    entity_id = int(argument_value)
    actor = _callback_actor(update)
    if action == "publish":
        session = ctx.publish_sessions.get(actor)
        if session is None or session.poll_id != entity_id:
            await ctx.responder.answer(callback_id, "start with /publish")
            return
        try:
            publish_result = await ctx.publish.publish(
                actor_user_id=actor,
                poll_id=entity_id,
                chat_ids=session.selected,
            )
        except DomainError as error:
            await ctx.responder.answer(callback_id, "failed")
            await ctx.responder.send(chat_id, str(error))
            return
        ctx.publish_sessions.discard(actor)
        await ctx.responder.answer(callback_id, "published")
        await ctx.responder.send(
            chat_id,
            f"published poll #{entity_id} as instance #{publish_result.instance.id} "
            f"to {len(publish_result.messages)} chat(s)",
        )
        return
    if action == "removechat":
        try:
            deactivation = await ctx.chats.deactivate(entity_id, actor_user_id=actor)
        except DomainError as error:
            await ctx.responder.answer(callback_id, "failed")
            await ctx.responder.send(chat_id, str(error))
            return
        await ctx.responder.answer(callback_id, "done")
        await ctx.responder.send(
            chat_id,
            f"chat #{entity_id} deactivated; {deactivation.skipped_messages} message(s) skipped",
        )
        return
    if action in {"close", "reopen", "delete"}:
        try:
            if action == "close":
                instance = await ctx.polls.close(entity_id, actor_user_id=actor)
                message = f"closed instance #{instance.id}"
            elif action == "reopen":
                instance = await ctx.polls.reopen(entity_id, actor_user_id=actor)
                message = f"reopened instance #{instance.id}"
            else:
                broadcast = await ctx.polls.delete(entity_id, actor_user_id=actor)
                message = f"deleted poll #{entity_id}; redacted {broadcast.edited} message(s)"
        except DomainError as error:
            await ctx.responder.answer(callback_id, "failed")
            await ctx.responder.send(chat_id, str(error))
            return
        await ctx.responder.answer(callback_id, "done")
        await ctx.responder.send(chat_id, message)
        return
    await ctx.responder.answer(callback_id, "unsupported action")


def _parse_triple(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    parts = value.split("_")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def _parse_pair(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    parts = value.split("_")
    if len(parts) != 2 or not all(part.lstrip("-").isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


async def _chats_by_id(ctx: HandlerContext, chat_ids: frozenset[int]) -> tuple[Chat, ...]:
    async with ctx.uow() as repos:
        chats = [await repos.chats.get(chat_id) for chat_id in sorted(chat_ids)]
    return tuple(chat for chat in chats if chat is not None)


#: Command name -> handler.
COMMANDS: dict[str, Callable[[object, HandlerContext], Awaitable[None]]] = {
    "start": start,
    "help": help_command,
    "version": version,
    "status": status,
    "cancel": cancel,
    "chats": chats,
    "addchat": addchat,
    "removechat": removechat,
    "polls": polls,
    "poll": poll,
    "newpoll": newpoll,
    "publish": publish,
    "close": close,
    "reopen": reopen,
    "refresh": refresh,
    "responses": responses,
    "deletepoll": deletepoll,
}


async def _route(update: object, ctx: HandlerContext) -> None:
    """Route one private-chat message to its command handler."""
    name = command_name(message_text(update))
    if name is None and ctx.wizards.get(user_id_of(update)) is not None:
        await handle_wizard_message(update, ctx)
        return
    handler = COMMANDS.get(name or "")
    if handler is None:
        chat_id = await _admit(update, ctx, name)
        if chat_id is not None:
            await ctx.responder.send(chat_id, "unknown command. Use /help to see the commands.")
        return
    await handler(update, ctx)


async def handle_command(update: object, ctx: HandlerContext) -> None:
    """Handle one message, converting an unexpected failure into a correlation-ID reply."""
    try:
        await _route(update, ctx)
    except Exception:
        correlation = uuid.uuid4().hex[:12]
        logger.exception("unhandled command error", extra={"correlation_id": correlation})
        message = getattr(update, "message", None)
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            return
        await ctx.responder.send(int(chat_id), f"unexpected error (reference {correlation})")


__all__ = [
    "COMMANDS",
    "HELP_TEXT",
    "HandlerContext",
    "Responder",
    "handle_callback",
    "handle_command",
]
