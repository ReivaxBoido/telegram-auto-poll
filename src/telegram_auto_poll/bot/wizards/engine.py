"""Pure new-poll wizard engine (FR-111, FR-116 - FR-118, FR-121, FR-122).

``advance(state, input, context)`` is a total function: every ``(step, event)`` pair yields a
transition, and nothing performs I/O. Validation reuses the domain validators, so the wizard cannot
accept a value the service would reject, and the error text is the same one the admin sees.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from enum import StrEnum
from typing import cast
from zoneinfo import ZoneInfo

from ...domain.entities import Poll, Schedule
from ...domain.enums import ScheduleKind
from ...domain.errors import ValidationError
from ...domain.validation import (
    OPTION_MAX,
    OPTION_MIN,
    validate_date_range,
    validate_day_of_month,
    validate_description,
    validate_question,
    validate_timezone,
    validate_weekdays,
)
from ...services.poll_service import ScheduleDraft
from ..callbacks import encode
from ..render import format_instant
from .states import WizardEvent, WizardInput, WizardState, WizardStep

UNDERSTOOD = (
    "that input is not understood at this step; use the buttons or send the requested value"
)


class WizardEffect(StrEnum):
    """What the handler must do after a transition."""

    PROMPT = "prompt"
    COMMIT = "commit"
    CANCELLED = "cancelled"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class WizardContext:
    """Everything the engine needs from the outside world."""

    default_timezone: str
    active_chat_ids: frozenset[int]
    now: datetime


@dataclass(frozen=True, slots=True)
class PollDraft:
    """A fully validated poll and optional schedule, ready for one transaction."""

    question: str
    options: tuple[str, ...]
    targets: tuple[int, ...]
    description: str | None = None
    is_anonymous: bool = True
    allows_multiple_answers: bool = False
    schedule: ScheduleDraft | None = None


@dataclass(frozen=True, slots=True)
class Transition:
    """The new state plus the handler's next action."""

    state: WizardState
    effect: WizardEffect
    draft: PollDraft | None = None


def _prompt(state: WizardState) -> Transition:
    return Transition(state=state, effect=WizardEffect.PROMPT)


def _stay(state: WizardState, error: str) -> Transition:
    return _prompt(replace(state, error=error))


def _forward(state: WizardState, step: WizardStep, **changes: object) -> WizardState:
    # ``changes`` is a partial WizardState; every call site passes real field names. A typed unpack
    # would need one helper per step, which is more code than it protects.
    return replace(
        state,
        step=step,
        error=None,
        history=(*state.history, state.step),
        **cast(typing.Any, changes),
    )


def _back(state: WizardState) -> Transition:
    if not state.history:
        return _prompt(state)
    return _prompt(replace(state, step=state.history[-1], history=state.history[:-1], error=None))


def _parse_time(text: str) -> time:
    try:
        hour, minute = text.strip().split(":", 1)
        return time(int(hour), int(minute))
    except ValueError as exc:
        raise ValidationError("time_of_day", "send the time as HH:MM, for example 09:30") from exc


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise ValidationError(
            "date", "send the date as YYYY-MM-DD, for example 2026-06-01"
        ) from exc


def _parse_run_at(text: str) -> datetime:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ValidationError(
            "run_at", "send the instant as YYYY-MM-DD HH:MM in the schedule timezone"
        ) from exc


def _build_schedule(state: WizardState, context: WizardContext) -> ScheduleDraft | None:
    if state.schedule_kind is None:
        return None
    run_at: datetime | None = None
    if state.schedule_kind is ScheduleKind.ONCE:
        if state.run_at_local is None:
            raise ValidationError("run_at", "send the one-off instant first")
        run_at = state.run_at_local.replace(tzinfo=ZoneInfo(state.timezone))
    created = Schedule.create(
        poll_id=0,
        kind=state.schedule_kind,
        timezone=state.timezone,
        time_of_day=state.time_of_day,
        weekdays=state.weekdays,
        day_of_month=state.day_of_month,
        run_at=run_at,
        start_date=state.start_date,
        end_date=state.end_date,
        now=context.now,
    )
    return ScheduleDraft(
        kind=created.kind,
        timezone=created.timezone,
        time_of_day=created.time_of_day,
        weekdays=created.weekdays,
        day_of_month=created.day_of_month,
        run_at=created.run_at,
        start_date=created.start_date,
        end_date=created.end_date,
    )


def _commit(state: WizardState, context: WizardContext) -> Transition:
    if state.question is None or len(state.options) < 2 or not state.targets:
        return _stay(state, "the poll is incomplete; go back and finish the missing fields")
    try:
        poll = Poll.create(
            question=state.question,
            options=state.options,
            description=state.description,
            is_anonymous=state.is_anonymous,
            allows_multiple_answers=state.allows_multiple_answers,
            targets=tuple(sorted(state.targets)),
            now=context.now,
        )
        schedule = _build_schedule(state, context)
    except ValidationError as exc:
        return _stay(state, str(exc))
    draft = PollDraft(
        question=poll.question,
        options=poll.options,
        targets=poll.targets,
        description=poll.description,
        is_anonymous=poll.is_anonymous,
        allows_multiple_answers=poll.allows_multiple_answers,
        schedule=schedule,
    )
    return Transition(
        state=replace(state, step=WizardStep.COMPLETED), effect=WizardEffect.COMMIT, draft=draft
    )


def _target_id(action: WizardInput) -> int | None:
    if isinstance(action.value, int):
        return action.value
    if isinstance(action.value, str) and action.value.lstrip("-").isdigit():
        return int(action.value)
    return None


def advance(state: WizardState, action: WizardInput, *, context: WizardContext) -> Transition:
    """Apply one event. Total over every ``(step, event)`` pair."""
    if state.is_terminal:
        return Transition(state=state, effect=WizardEffect.IGNORED)
    if action.kind in {WizardEvent.CANCEL, WizardEvent.TIMEOUT}:
        return Transition(
            state=replace(state, step=WizardStep.CANCELLED, error=None),
            effect=WizardEffect.CANCELLED,
        )
    if action.kind is WizardEvent.BACK:
        return _back(state)

    step = state.step
    if step is WizardStep.QUESTION:
        return _question(state, action)
    if step is WizardStep.DESCRIPTION:
        return _description(state, action)
    if step is WizardStep.OPTIONS:
        return _options(state, action)
    if step is WizardStep.ANONYMITY:
        return _anonymity(state, action)
    if step is WizardStep.MULTIPLE:
        return _multiple(state, action)
    if step is WizardStep.TARGETS:
        return _targets(state, action, context)
    if step is WizardStep.SCHEDULE_KIND:
        return _schedule_kind(state, action)
    if step is WizardStep.SCHEDULE_WEEKDAYS:
        return _weekdays(state, action)
    if step is WizardStep.SCHEDULE_DAY:
        return _day_of_month(state, action)
    if step is WizardStep.SCHEDULE_TIME:
        return _time_of_day(state, action)
    if step is WizardStep.SCHEDULE_RUN_AT:
        return _run_at(state, action)
    if step is WizardStep.SCHEDULE_TIMEZONE:
        return _timezone(state, action, context)
    if step is WizardStep.SCHEDULE_START:
        return _start_date(state, action)
    if step is WizardStep.SCHEDULE_END:
        return _end_date(state, action, context)
    return _preview(state, action, context)


def _text(action: WizardInput) -> str | None:
    return action.text.strip() if action.text is not None else None


def _question(state: WizardState, action: WizardInput) -> Transition:
    text = _text(action)
    if action.kind is not WizardEvent.TEXT or text is None:
        return _stay(state, UNDERSTOOD)
    try:
        question = validate_question(text)
    except ValidationError as exc:
        return _stay(state, str(exc))
    return _prompt(_forward(state, WizardStep.DESCRIPTION, question=question))


def _description(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is WizardEvent.SKIP:
        return _prompt(_forward(state, WizardStep.OPTIONS, description=None))
    text = _text(action)
    if action.kind is not WizardEvent.TEXT or text is None:
        return _stay(state, UNDERSTOOD)
    try:
        description = validate_description(text)
    except ValidationError as exc:
        return _stay(state, str(exc))
    return _prompt(_forward(state, WizardStep.OPTIONS, description=description))


def _options(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is WizardEvent.OPTION_DONE:
        if len(state.options) < 2:
            return _stay(state, "add at least two options before continuing")
        return _prompt(_forward(state, WizardStep.ANONYMITY))
    text = _text(action)
    if action.kind is not WizardEvent.TEXT or text is None:
        return _stay(state, UNDERSTOOD)
    if len(state.options) >= 12:
        return _stay(state, "a poll can have at most 12 options")
    option = text.strip()
    if not OPTION_MIN <= len(option) <= OPTION_MAX:
        return _stay(state, f"an option must be {OPTION_MIN}-{OPTION_MAX} characters")
    if option.casefold() in {existing.casefold() for existing in state.options}:
        return _stay(state, f"options must be unique ('{option}' is repeated)")
    return _prompt(replace(state, options=(*state.options, option), error=None))


def _anonymity(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is WizardEvent.TOGGLE:
        return _prompt(replace(state, error=None, is_anonymous=not state.is_anonymous))
    if action.kind is WizardEvent.NEXT:
        return _prompt(_forward(state, WizardStep.MULTIPLE))
    return _stay(state, UNDERSTOOD)


def _multiple(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is WizardEvent.TOGGLE:
        return _prompt(
            replace(state, error=None, allows_multiple_answers=not state.allows_multiple_answers)
        )
    if action.kind is WizardEvent.NEXT:
        return _prompt(_forward(state, WizardStep.TARGETS))
    return _stay(state, UNDERSTOOD)


def _targets(state: WizardState, action: WizardInput, context: WizardContext) -> Transition:
    if action.kind is WizardEvent.PAGE:
        if not isinstance(action.value, int):
            return _stay(state, UNDERSTOOD)
        return _prompt(replace(state, chat_page=max(action.value, 1), error=None))
    if action.kind is WizardEvent.TOGGLE:
        chat_id = _target_id(action)
        if chat_id is None or chat_id not in context.active_chat_ids:
            return _stay(state, "that chat is not registered and active; pick one from the list")
        selected = set(state.targets)
        selected.symmetric_difference_update({chat_id})
        return _prompt(replace(state, targets=frozenset(selected), error=None))
    if action.kind is WizardEvent.NEXT:
        if not state.targets:
            return _stay(state, "select at least one active chat")
        return _prompt(_forward(state, WizardStep.SCHEDULE_KIND))
    return _stay(state, UNDERSTOOD)


def _schedule_kind(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is not WizardEvent.SELECT or action.value is None:
        return _stay(state, UNDERSTOOD)
    choice = str(action.value)
    if choice == "none":
        return _prompt(_forward(state, WizardStep.PREVIEW, schedule_kind=None))
    try:
        kind = ScheduleKind(choice)
    except ValueError:
        return _stay(state, "choose daily, weekly, monthly, one-off, or save without a schedule")
    following = {
        ScheduleKind.DAILY: WizardStep.SCHEDULE_TIME,
        ScheduleKind.WEEKLY: WizardStep.SCHEDULE_WEEKDAYS,
        ScheduleKind.MONTHLY: WizardStep.SCHEDULE_DAY,
        ScheduleKind.ONCE: WizardStep.SCHEDULE_RUN_AT,
    }[kind]
    return _prompt(_forward(state, following, schedule_kind=kind))


def _weekdays(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is WizardEvent.WEEKDAY:
        if not isinstance(action.value, int) or not 1 <= action.value <= 7:
            return _stay(state, "weekdays must be between 1 and 7")
        selected = set(state.weekdays)
        selected.symmetric_difference_update({action.value})
        return _prompt(replace(state, weekdays=tuple(sorted(selected)), error=None))
    text = _text(action)
    if action.kind is not WizardEvent.TEXT or text is None:
        return _stay(state, UNDERSTOOD)
    try:
        days = validate_weekdays([int(part) for part in text.replace(" ", "").split(",")])
    except (ValueError, ValidationError) as exc:
        message = (
            str(exc) if isinstance(exc, ValidationError) else "send weekdays as 1,4 (1=Monday)"
        )
        return _stay(state, message)
    return _prompt(_forward(state, WizardStep.SCHEDULE_TIME, weekdays=days))


def _day_of_month(state: WizardState, action: WizardInput) -> Transition:
    text = _text(action)
    if action.kind is not WizardEvent.TEXT or text is None or not text.isdigit():
        return _stay(state, "send the day of the month as a number from 1 to 31")
    try:
        day = validate_day_of_month(int(text))
    except ValidationError as exc:
        return _stay(state, str(exc))
    return _prompt(_forward(state, WizardStep.SCHEDULE_TIME, day_of_month=day))


def _time_of_day(state: WizardState, action: WizardInput) -> Transition:
    text = _text(action)
    if action.kind is not WizardEvent.TEXT or text is None:
        return _stay(state, UNDERSTOOD)
    try:
        at = _parse_time(text)
    except ValidationError as exc:
        return _stay(state, str(exc))
    return _prompt(_forward(state, WizardStep.SCHEDULE_TIMEZONE, time_of_day=at))


def _run_at(state: WizardState, action: WizardInput) -> Transition:
    text = _text(action)
    if action.kind is not WizardEvent.TEXT or text is None:
        return _stay(state, UNDERSTOOD)
    try:
        moment = _parse_run_at(text)
    except ValidationError as exc:
        return _stay(state, str(exc))
    return _prompt(_forward(state, WizardStep.SCHEDULE_TIMEZONE, run_at_local=moment))


def _timezone(state: WizardState, action: WizardInput, context: WizardContext) -> Transition:
    if action.kind is WizardEvent.SKIP:
        return _prompt(
            _forward(state, WizardStep.SCHEDULE_START, timezone=context.default_timezone)
        )
    text = _text(action)
    if action.kind is not WizardEvent.TEXT or text is None:
        return _stay(state, UNDERSTOOD)
    try:
        zone = validate_timezone(text)
    except ValidationError as exc:
        return _stay(state, str(exc))
    return _prompt(_forward(state, WizardStep.SCHEDULE_START, timezone=zone))


def _start_date(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is WizardEvent.SKIP:
        return _prompt(_forward(state, WizardStep.SCHEDULE_END, start_date=None))
    text = _text(action)
    if action.kind is not WizardEvent.TEXT or text is None:
        return _stay(state, UNDERSTOOD)
    try:
        start = _parse_date(text)
        validate_date_range(start, state.end_date)
    except ValidationError as exc:
        return _stay(state, str(exc))
    return _prompt(_forward(state, WizardStep.SCHEDULE_END, start_date=start))


def _end_date(state: WizardState, action: WizardInput, context: WizardContext) -> Transition:
    end: date | None = None
    if action.kind is WizardEvent.TEXT:
        text = _text(action)
        if text is None:
            return _stay(state, UNDERSTOOD)
        try:
            end = _parse_date(text)
            validate_date_range(state.start_date, end)
        except ValidationError as exc:
            return _stay(state, str(exc))
    elif action.kind is not WizardEvent.SKIP:
        return _stay(state, UNDERSTOOD)

    candidate = _forward(state, WizardStep.PREVIEW, end_date=end)
    try:
        _build_schedule(candidate, context)
    except ValidationError as exc:
        return _stay(state, str(exc))
    return _prompt(candidate)


def _preview(state: WizardState, action: WizardInput, context: WizardContext) -> Transition:
    if action.kind is not WizardEvent.CONFIRM:
        return _stay(state, "press Confirm to create the poll, or Back to change something")
    return _commit(state, context)


def schedule_preview(state: WizardState, context: WizardContext) -> tuple[str, ...]:
    """Render the next five occurrences for the preview screen (FR-118)."""
    schedule = _build_schedule(state, context)
    if schedule is None:
        return ()
    entity = Schedule.create(
        poll_id=0,
        kind=schedule.kind,
        timezone=schedule.timezone,
        time_of_day=schedule.time_of_day,
        weekdays=schedule.weekdays,
        day_of_month=schedule.day_of_month,
        run_at=schedule.run_at,
        start_date=schedule.start_date,
        end_date=schedule.end_date,
        now=context.now,
    )
    from ...domain.schedule_math import next_occurrences

    return tuple(
        format_instant(run) for run in next_occurrences(entity, after=context.now, count=5)
    )


def option_keyboard_data(index: int) -> str:
    """Callback data for removing option ``index`` from the list."""
    return encode("w", "option_remove", index)
