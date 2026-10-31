"""Admin command handlers (FR-109, FR-112 - FR-119, FR-123, FR-124, FR-132).

Handlers are plain coroutines taking ``(update, context)``: no dispatcher, no PTB ``CallbackContext``,
and replies go through the ``Responder`` port. That keeps every command directly invocable in tests
with a fake update and a recording responder.
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
from ...domain.errors import DomainError, ValidationError
from ...domain.ports import Clock
from ...domain.schedule_math import next_occurrences
from ...services.chat_service import ChatService, parse_chat_id
from ...services.delivery_service import DeliveryService
from ...services.poll_service import PollService
from ...services.schedule_service import ScheduleService
from ..auth import GENERIC_REFUSAL, AdminGuard, AuthDenial, command_name, rate_limit_message
from ..keyboards import confirmation_keyboard, pagination_keyboard
from ..render import (
    chat_line,
    empty_listing,
    page_hint,
    poll_detail,
    poll_line,
    schedule_line,
    schedule_view,
    status_message,
)
from ..wizards.engine import WizardContext, WizardEffect, advance, schedule_preview
from ..wizards.states import WizardEvent, WizardInput, WizardState, WizardStep
from ..wizards.store import WizardStore

logger = logging.getLogger(__name__)

HELP_TEXT = "\n".join(
    [
        "Commands:",
        "/start /help /version /status",
        "/chats [page] /addchat /removechat <id>",
        "/polls [page] /poll <id> /newpoll",
        "/schedules [poll_id] /schedule <id>",
        "/pause <id> /resume <id> /delete_schedule <id>",
        "/sendnow <id> /cancel",
    ]
)

CONFIRM_ACTIONS = {
    "removechat": "chat",
    "delete_schedule": "schedule",
    "sendnow": "send",
}


class Responder(Protocol):
    """Sends messages and answers callback queries."""

    async def send(
        self, chat_id: int, text: str, *, keyboard: InlineKeyboardMarkup | None = None
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
    schedules: ScheduleService
    deliveries: DeliveryService
    uow: UnitOfWork
    wizards: WizardStore
    clock: Clock
    responder: Responder
    version: str
    build_sha: str
    started_at: datetime
    default_timezone: str
    heartbeat_age: Callable[[], float | None]


def chat_id_of(update: object) -> int:
    """Return the chat id of a private message update.

    Raises:
        ValueError: the update carries no message (callers authorize messages only).
    """
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
    """Parse a required positive integer argument.

    Raises:
        ValidationError: missing, non-numeric, or below ``minimum``.
    """
    if value is None or not value.lstrip("-").isdigit():
        raise ValidationError(field, f"send a numeric {field}")
    number = int(value)
    if number < minimum:
        raise ValidationError(field, f"{field} must be at least {minimum}")
    return number


async def _admit(update: object, ctx: HandlerContext, command: str | None) -> int | None:
    """Authorize and charge one command; reply on refusal. Returns the chat id or ``None``."""
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
    """/status: health, counts, next runs, and recent failures (FR-109)."""
    chat_id = await _admit(update, ctx, "status")
    if chat_id is None:
        return
    now = ctx.clock.now()
    async with ctx.uow() as repos:
        chats = await repos.chats.count(active_only=True)
        polls = await repos.polls.count(active_only=True)
        schedules = await repos.schedules.list_all(active_only=True)
        deliveries = await repos.deliveries.counts()
        failures = await repos.deliveries.list_recent_failures(limit=5)

    uptime = (now - ctx.started_at).total_seconds()
    lines = [
        status_message(
            version=ctx.version,
            build_sha=ctx.build_sha,
            uptime_seconds=uptime,
            db_ok=True,
            heartbeat_age=ctx.heartbeat_age(),
        ),
        f"chats: {chats} active",
        f"polls: {polls} active",
        f"schedules: {len(schedules)} active",
        f"deliveries: {deliveries}",
    ]
    upcoming = [
        run for schedule in schedules for run in next_occurrences(schedule, after=now, count=1)
    ][:5]
    lines.append("next runs: " + (", ".join(run.isoformat() for run in upcoming) or "none"))
    if failures:
        lines.append("recent failures:")
        lines.extend(
            f"  #{delivery.id} chat {delivery.chat_id}: {delivery.last_error or 'unknown'}"
            for delivery in failures
        )
    await ctx.responder.send(chat_id, "\n".join(lines))


async def cancel(update: object, ctx: HandlerContext) -> None:
    """/cancel: drop the admin's wizard session."""
    chat_id = await _admit(update, ctx, "cancel")
    if chat_id is None:
        return
    had_session = ctx.wizards.get(user_id_of(update)) is not None
    ctx.wizards.discard(user_id_of(update))
    await ctx.responder.send(
        chat_id, "wizard cancelled" if had_session else "there is no wizard to cancel"
    )


# --------------------------------------------------------------------------------------------
# Chats
# --------------------------------------------------------------------------------------------


async def chats(update: object, ctx: HandlerContext) -> None:
    """/chats [page]: paginated registered chats (FR-115)."""
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
    """/addchat: register by forwarded message or pasted id (FR-112 - FR-114)."""
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
        f"deactivate #{chat.id} {chat.title}? pending deliveries become skipped and "
        "schedules without another target are paused.",
        keyboard=confirmation_keyboard(
            entity="chat",
            entity_id=chat.id or target,
            confirm_action="confirm",
            namespace="w",
        ),
    )


# --------------------------------------------------------------------------------------------
# Polls
# --------------------------------------------------------------------------------------------


async def polls(update: object, ctx: HandlerContext) -> None:
    """/polls [page]: paginated poll summaries (FR-119)."""
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
    body = "\n".join(poll_line(poll) for poll in page.items)
    await ctx.responder.send(chat_id, f"{body}\n\n{page_hint(page)}", keyboard=keyboard)


async def poll(update: object, ctx: HandlerContext) -> None:
    """/poll <id>: definition, targets, schedules, next runs."""
    chat_id = await _admit(update, ctx, "poll")
    if chat_id is None:
        return
    try:
        poll_id = parse_int(argument(update), field="poll_id")
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    try:
        detail = await ctx.polls.detail(poll_id)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    await ctx.responder.send(chat_id, poll_detail(detail))


async def sendnow(update: object, ctx: HandlerContext) -> None:
    """/sendnow <id>: show a confirmation; the send is queued on the callback."""
    chat_id = await _admit(update, ctx, "sendnow")
    if chat_id is None:
        return
    try:
        poll_id = parse_int(argument(update), field="poll_id")
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    try:
        detail = await ctx.polls.detail(poll_id)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    await ctx.responder.send(
        chat_id,
        f"send poll #{poll_id} to {len(detail.poll.targets)} active target(s) now?",
        keyboard=confirmation_keyboard(
            entity="send",
            entity_id=poll_id,
            confirm_action="confirm",
            namespace="w",
        ),
    )


# --------------------------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------------------------


async def schedules(update: object, ctx: HandlerContext) -> None:
    """/schedules [poll_id]: paginated schedules (FR-123)."""
    chat_id = await _admit(update, ctx, "schedules")
    if chat_id is None:
        return
    try:
        raw = argument(update)
        poll_id = parse_int(raw, field="poll_id") if raw else None
        page = await ctx.schedules.page(1, poll_id=poll_id)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    if not page.items:
        await ctx.responder.send(chat_id, "no schedules yet. Use /newpoll to add one.")
        return
    body = "\n".join(schedule_line(schedule) for schedule in page.items)
    await ctx.responder.send(chat_id, f"{body}\n\n{page_hint(page)}")


async def schedule(update: object, ctx: HandlerContext) -> None:
    """/schedule <id>: detail with the next five occurrences."""
    chat_id = await _admit(update, ctx, "schedule")
    if chat_id is None:
        return
    try:
        schedule_id = parse_int(argument(update), field="schedule_id")
        detail = await ctx.schedules.detail(schedule_id)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    await ctx.responder.send(chat_id, schedule_view(detail))


async def _set_paused(update: object, ctx: HandlerContext, *, paused: bool) -> None:
    command = "pause" if paused else "resume"
    chat_id = await _admit(update, ctx, command)
    if chat_id is None:
        return
    try:
        schedule_id = parse_int(argument(update), field="schedule_id")
        if paused:
            updated = await ctx.schedules.pause(schedule_id, actor_user_id=user_id_of(update))
        else:
            updated = await ctx.schedules.resume(schedule_id, actor_user_id=user_id_of(update))
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    await ctx.responder.send(chat_id, f"schedule #{schedule_id}: {schedule_line(updated)}")


async def pause(update: object, ctx: HandlerContext) -> None:
    """/pause <schedule_id>."""
    await _set_paused(update, ctx, paused=True)


async def resume(update: object, ctx: HandlerContext) -> None:
    """/resume <schedule_id>."""
    await _set_paused(update, ctx, paused=False)


async def delete_schedule(update: object, ctx: HandlerContext) -> None:
    """/delete_schedule <id>: show a confirmation; deletion happens on the callback."""
    chat_id = await _admit(update, ctx, "delete_schedule")
    if chat_id is None:
        return
    try:
        schedule_id = parse_int(argument(update), field="schedule_id")
        detail = await ctx.schedules.detail(schedule_id)
    except DomainError as error:
        await _reply_error(chat_id, ctx, error)
        return
    await ctx.responder.send(
        chat_id,
        f"delete schedule #{schedule_id} ({schedule_line(detail.schedule)})? "
        "Poll, chats, and delivery history are kept.",
        keyboard=confirmation_keyboard(
            entity="schedule",
            entity_id=schedule_id,
            confirm_action="confirm",
            namespace="w",
        ),
    )


async def newpoll(update: object, ctx: HandlerContext) -> None:
    """/newpoll: start a wizard session and ask for the question."""
    chat_id = await _admit(update, ctx, "newpoll")
    if chat_id is None:
        return
    admin = user_id_of(update)
    if ctx.wizards.get(admin) is not None:
        await ctx.responder.send(
            chat_id, "a wizard is already open. Finish it, or send /cancel and start again."
        )
        return

    ctx.wizards.put(WizardState(admin_user_id=admin, timezone=ctx.default_timezone))
    await ctx.responder.send(chat_id, _prompt("Send the poll question (1-300 characters)."))


def _wizard_input(state: WizardState, text: str) -> WizardInput:

    step = state.step
    lowered = text.strip().lower()
    if lowered == "back":
        return WizardInput(WizardEvent.BACK)
    if lowered == "cancel":
        return WizardInput(WizardEvent.CANCEL)
    if lowered == "skip":
        return WizardInput(WizardEvent.SKIP)
    if lowered == "done":
        return WizardInput(WizardEvent.OPTION_DONE)
    if lowered == "next":
        return WizardInput(WizardEvent.NEXT)
    if lowered == "confirm":
        return WizardInput(WizardEvent.CONFIRM)
    if step is WizardStep.TARGETS and text.strip().lstrip("-").isdigit():
        return WizardInput(WizardEvent.TOGGLE, value=int(text.strip()))
    if step is WizardStep.SCHEDULE_KIND:
        return WizardInput(WizardEvent.SELECT, value=lowered)
    if step in {WizardStep.ANONYMITY, WizardStep.MULTIPLE}:
        return WizardInput(WizardEvent.TOGGLE)
    return WizardInput(WizardEvent.TEXT, text=text)


async def _active_chats(ctx: HandlerContext) -> frozenset[int]:
    async with ctx.uow() as repos:
        return frozenset(await repos.chats.active_ids())


def render_step(state: WizardState, context: WizardContext) -> str:
    """Render the prompt for the current wizard step."""

    step = state.step
    if step is WizardStep.QUESTION:
        return _prompt("Send the poll question (1-300 characters).")
    if step is WizardStep.DESCRIPTION:
        return _prompt("Send the description (up to 1024 characters), or send 'skip'.")
    if step is WizardStep.OPTIONS:
        listed = ", ".join(state.options) or "(none yet)"
        return _prompt(f"Options: {listed}\nSend the next option, or 'done' to continue.")
    if step is WizardStep.ANONYMITY:
        value = "anonymous" if state.is_anonymous else "named"
        return _prompt(f"Current: {value}. Send 'toggle' to change, or 'next'.")
    if step is WizardStep.MULTIPLE:
        value = "multiple answers" if state.allows_multiple_answers else "single answer"
        return _prompt(f"Current: {value}. Send 'toggle' to change, or 'next'.")
    active_chats = context.active_chat_ids
    if step is WizardStep.TARGETS:
        chats_list = ", ".join(f"#{chat_id}" for chat_id in sorted(active_chats)) or "(none)"
        selected = ", ".join(f"#{chat_id}" for chat_id in sorted(state.targets)) or "(none)"
        return _prompt(
            f"Active chats: {chats_list}\nselected: {selected}\n"
            "Send a chat id to toggle it, then 'next'."
        )
    if step is WizardStep.SCHEDULE_KIND:
        return _prompt("Send daily, weekly, monthly, once, or none (save without a schedule).")
    if step is WizardStep.SCHEDULE_WEEKDAYS:
        return _prompt("Send the weekdays as ISO numbers, for example 1,4 (1=Monday).")
    if step is WizardStep.SCHEDULE_DAY:
        return _prompt("Send the day of the month (1-31). Days above 28 warn about short months.")
    if step is WizardStep.SCHEDULE_TIME:
        return _prompt("Send the local time as HH:MM, for example 09:30.")
    if step is WizardStep.SCHEDULE_RUN_AT:
        return _prompt("Send the one-off instant as YYYY-MM-DD HH:MM in the schedule timezone.")
    if step is WizardStep.SCHEDULE_TIMEZONE:
        return _prompt(f"Send an IANA timezone (current default: {state.timezone}), or 'skip'.")
    if step is WizardStep.SCHEDULE_START:
        return _prompt("Send a start date as YYYY-MM-DD, or 'skip'.")
    if step is WizardStep.SCHEDULE_END:
        return _prompt("Send an end date as YYYY-MM-DD, or 'skip'.")
    if step is WizardStep.CANCELLED:
        return "wizard cancelled"

    preview = schedule_preview(state, context)
    schedule_kind = state.schedule_kind
    schedule_text = "none" if schedule_kind is None else f"{schedule_kind} {state.timezone}"
    targets = ", ".join(f"#{chat_id}" for chat_id in sorted(state.targets))
    lines = [
        "Preview",
        f"question: {state.question}",
        f"options: {', '.join(state.options)}",
        f"targets: {targets}",
        f"schedule: {schedule_text}",
        "next runs: " + (", ".join(preview) or "none"),
        "",
        "Send 'confirm' to create the poll, 'back' to edit, /cancel to abort.",
    ]
    return "\n".join(lines)


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

    active = await _active_chats(ctx)
    context = WizardContext(
        default_timezone=ctx.default_timezone, active_chat_ids=active, now=ctx.clock.now()
    )
    text = message_text(update) or ""
    transition = advance(pending, _wizard_input(pending, text), context=context)

    if transition.effect is WizardEffect.COMMIT and transition.draft is not None:
        draft = transition.draft
        try:
            created = await ctx.polls.create(
                actor_user_id=admin,
                question=draft.question,
                options=draft.options,
                target_chat_ids=draft.targets,
                description=draft.description,
                is_anonymous=draft.is_anonymous,
                allows_multiple_answers=draft.allows_multiple_answers,
                schedule=draft.schedule,
            )
        except DomainError as error:
            ctx.wizards.put(pending)
            await ctx.responder.send(chat_id, f"not created: {error}")
            return
        ctx.wizards.discard(admin)
        schedule_id = created.schedule.id if created.schedule is not None else None
        suffix = f" with schedule #{schedule_id}" if schedule_id is not None else ""
        await ctx.responder.send(chat_id, f"created poll #{created.poll.id}{suffix}")
        return

    if transition.effect is WizardEffect.CANCELLED:
        ctx.wizards.discard(admin)
        await ctx.responder.send(chat_id, "wizard cancelled")
        return

    ctx.wizards.put(transition.state)
    if transition.effect is WizardEffect.IGNORED:
        await ctx.responder.send(chat_id, "this wizard is already finished")
        return

    prompt = render_step(transition.state, context)
    if transition.state.error:
        prompt = f"{transition.state.error}\n\n{prompt}"
    await ctx.responder.send(chat_id, prompt)


# --------------------------------------------------------------------------------------------
# Confirmation and wizard callbacks
# --------------------------------------------------------------------------------------------

CONFIRMATION_ACTIONS = frozenset({"chat", "schedule", "send"})


def _callback_query(update: object) -> object:
    query: object = update.callback_query  # type: ignore[attr-defined]
    return query


def _callback_chat_id(update: object) -> int:
    message = getattr(_callback_query(update), "message", None)
    chat = getattr(message, "chat", None)
    return int(getattr(chat, "id", 0))


def _callback_actor(update: object) -> int:
    return int(getattr(getattr(_callback_query(update), "from_user", None), "id", 0))


async def handle_callback(update: object, ctx: HandlerContext) -> None:
    """Answer or execute one inline-keyboard callback (FR-107, FR-115, FR-132)."""
    from ..callbacks import CallbackDataError, decode

    query = _callback_query(update)
    callback_id = str(getattr(query, "id", ""))
    decision = ctx.guard.authorize_callback(update)
    if not decision.allowed:
        await ctx.responder.answer(callback_id, "not allowed")
        return
    try:
        data = decode(str(getattr(query, "data", "")))
    except CallbackDataError:
        await ctx.responder.answer(callback_id, "invalid action")
        return

    chat_id = _callback_chat_id(update)
    actor = _callback_actor(update)

    if data.namespace == "w" and data.action == "cancel":
        ctx.wizards.discard(actor)
        await ctx.responder.answer(callback_id, "cancelled")
        return

    if data.namespace == "w" and data.action == "option_remove":
        pending = ctx.wizards.get(actor)
        if pending is None or pending.step is not WizardStep.OPTIONS:
            await ctx.responder.answer(callback_id, "the wizard is no longer on options")
            return
        index = int(data.argument or -1)
        if not 0 <= index < len(pending.options):
            await ctx.responder.answer(callback_id, "invalid option")
            return

        updated = replace(
            pending,
            options=tuple(
                option for position, option in enumerate(pending.options) if position != index
            ),
        )
        ctx.wizards.put(updated)
        await ctx.responder.answer(callback_id, "removed")

        option_context = WizardContext(
            default_timezone=ctx.default_timezone,
            active_chat_ids=await _active_chats(ctx),
            now=ctx.clock.now(),
        )
        await ctx.responder.send(chat_id, render_step(updated, option_context))
        return

    if data.namespace == "w" and data.action == "confirm" and data.argument:
        entity, _, raw_id = data.argument.partition("_")
        if entity not in CONFIRMATION_ACTIONS or not raw_id.isdigit():
            await ctx.responder.answer(callback_id, "invalid action")
            return
        identifier = int(raw_id)
        try:
            if entity == "chat":
                result = await ctx.chats.deactivate(identifier, actor_user_id=actor)
                message = f"chat #{identifier} deactivated; {result.skipped_deliveries} delivery row(s) skipped"
            elif entity == "schedule":
                await ctx.schedules.delete(identifier, actor_user_id=actor)
                message = f"schedule #{identifier} deleted"
            else:
                queued = await ctx.deliveries.send_now(identifier, actor_user_id=actor)
                message = f"queued {queued} delivery row(s) for poll #{identifier}"
        except DomainError as error:
            await ctx.responder.answer(callback_id, "failed")
            await ctx.responder.send(chat_id, str(error))
            return
        await ctx.responder.answer(callback_id, "done")
        await ctx.responder.send(chat_id, message)
        return

    await ctx.responder.answer(callback_id, "unsupported action")


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
    "sendnow": sendnow,
    "schedules": schedules,
    "schedule": schedule,
    "pause": pause,
    "resume": resume,
    "delete_schedule": delete_schedule,
    "newpoll": newpoll,
}


async def _route(update: object, ctx: HandlerContext) -> None:
    """Route one private-chat message to its command handler.

    Unknown commands and plain text receive a short hint; the wizard will intercept plain text
    before this router once it is wired.
    """
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


# --------------------------------------------------------------------------------------------
# New-poll wizard
# --------------------------------------------------------------------------------------------


def _prompt(text: str) -> str:
    return f"{text}\n\n(back = previous step, /cancel = abort)"


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
