"""Wizard engine and store (T-042, FR-111, FR-116 - FR-118, FR-121, FR-122).

Every transition is exercised with a fixed instant and a fixed chat list; the store is driven with a
fake clock. No handler, no Telegram, no I/O.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from telegram_auto_poll.bot.wizards.engine import (
    UNDERSTOOD,
    PollDraft,
    WizardContext,
    WizardEffect,
    advance,
    option_keyboard_data,
    schedule_preview,
)
from telegram_auto_poll.bot.wizards.states import (
    TERMINAL_STEPS,
    WizardEvent,
    WizardInput,
    WizardState,
    WizardStep,
)
from telegram_auto_poll.bot.wizards.store import WizardStore
from telegram_auto_poll.domain.enums import ScheduleKind
from tests.factories import FIXED_NOW
from tests.fakes import FakeClock

ADMIN = 100_000_001
CHAT = 1
OTHER_CHAT = 2
CONTEXT = WizardContext(
    default_timezone="UTC", active_chat_ids=frozenset({CHAT, OTHER_CHAT}), now=FIXED_NOW
)


def state(step: WizardStep, **fields: object) -> WizardState:
    defaults: dict[str, object] = {
        "admin_user_id": ADMIN,
        "step": step,
        "question": "Ship it?",
        "options": ("Yes", "No"),
        "targets": frozenset({CHAT}),
        "timezone": "UTC",
    }
    return WizardState(**{**defaults, **fields})  # type: ignore[arg-type]


def send(state_: WizardState, kind: WizardEvent, **payload: object) -> object:
    return advance(state_, WizardInput(kind=kind, **payload), context=CONTEXT)  # type: ignore[arg-type]


def text(state_: WizardState, value: str) -> object:
    return send(state_, WizardEvent.TEXT, text=value)


def walk(state_: WizardState, steps: list[tuple[WizardEvent, dict[str, object]]]) -> object:
    transition = None
    for kind, payload in steps:
        transition = advance(state_, WizardInput(kind=kind, **payload), context=CONTEXT)  # type: ignore[arg-type]
        state_ = transition.state
    return transition


# --------------------------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------------------------


def test_daily_wizard_reaches_commit_with_the_collected_values() -> None:
    transition = walk(
        state(WizardStep.QUESTION, question=None, options=(), targets=frozenset()),
        [
            (WizardEvent.TEXT, {"text": "What next?"}),
            (WizardEvent.TEXT, {"text": "Pick one"}),
            (WizardEvent.TEXT, {"text": "Speed"}),
            (WizardEvent.TEXT, {"text": "Docs"}),
            (WizardEvent.OPTION_DONE, {}),
            (WizardEvent.TOGGLE, {}),  # anonymous -> false
            (WizardEvent.NEXT, {}),
            (WizardEvent.TOGGLE, {}),  # multiple -> true
            (WizardEvent.NEXT, {}),
            (WizardEvent.TOGGLE, {"value": CHAT}),
            (WizardEvent.NEXT, {}),
            (WizardEvent.SELECT, {"value": "daily"}),
            (WizardEvent.TEXT, {"text": "09:00"}),
            (WizardEvent.SKIP, {}),  # timezone default
            (WizardEvent.SKIP, {}),  # start
            (WizardEvent.SKIP, {}),  # end
            (WizardEvent.CONFIRM, {}),
        ],
    )

    assert transition.effect is WizardEffect.COMMIT  # type: ignore[attr-defined]
    assert transition.state.step is WizardStep.COMPLETED  # type: ignore[attr-defined]
    draft = transition.draft  # type: ignore[attr-defined]
    assert isinstance(draft, PollDraft)
    assert draft.question == "What next?"
    assert draft.description == "Pick one"
    assert draft.options == ("Speed", "Docs")
    assert draft.is_anonymous is False
    assert draft.allows_multiple_answers is True
    assert draft.targets == (CHAT,)
    assert draft.schedule is not None
    assert draft.schedule.kind is ScheduleKind.DAILY
    assert draft.schedule.time_of_day == time(9, 0)


def test_description_can_be_skipped() -> None:
    transition = send(state(WizardStep.DESCRIPTION), WizardEvent.SKIP)

    assert transition.state.step is WizardStep.OPTIONS  # type: ignore[attr-defined]
    assert transition.state.description is None  # type: ignore[attr-defined]


def test_weekly_wizard_keeps_the_weekdays() -> None:
    transition = walk(
        state(WizardStep.SCHEDULE_KIND),
        [
            (WizardEvent.SELECT, {"value": "weekly"}),
            (WizardEvent.TEXT, {"text": "1,4"}),
            (WizardEvent.TEXT, {"text": "18:30"}),
            (WizardEvent.SKIP, {}),
            (WizardEvent.SKIP, {}),
            (WizardEvent.SKIP, {}),
        ],
    )

    assert transition.state.step is WizardStep.PREVIEW  # type: ignore[attr-defined]
    assert transition.state.weekdays == (1, 4)  # type: ignore[attr-defined]


def test_monthly_wizard_keeps_the_day() -> None:
    transition = walk(
        state(WizardStep.SCHEDULE_KIND),
        [
            (WizardEvent.SELECT, {"value": "monthly"}),
            (WizardEvent.TEXT, {"text": "15"}),
            (WizardEvent.TEXT, {"text": "09:00"}),
            (WizardEvent.SKIP, {}),
            (WizardEvent.SKIP, {}),
            (WizardEvent.SKIP, {}),
        ],
    )

    assert transition.state.day_of_month == 15  # type: ignore[attr-defined]


def test_one_off_is_converted_to_utc_with_the_schedule_timezone() -> None:
    transition = walk(
        state(WizardStep.SCHEDULE_KIND),
        [
            (WizardEvent.SELECT, {"value": "once"}),
            (WizardEvent.TEXT, {"text": "2026-06-01 18:30"}),
            (WizardEvent.TEXT, {"text": "Europe/London"}),
            (WizardEvent.SKIP, {}),
            (WizardEvent.SKIP, {}),
            (WizardEvent.CONFIRM, {}),
        ],
    )

    draft = transition.draft  # type: ignore[attr-defined]
    assert draft is not None
    assert draft.schedule is not None
    assert draft.schedule.run_at == datetime(2026, 6, 1, 17, 30, tzinfo=UTC)


def test_save_without_schedule_commits_a_poll_only() -> None:
    transition = walk(
        state(WizardStep.SCHEDULE_KIND),
        [
            (WizardEvent.SELECT, {"value": "none"}),
            (WizardEvent.CONFIRM, {}),
        ],
    )

    assert transition.effect is WizardEffect.COMMIT  # type: ignore[attr-defined]
    assert transition.draft.schedule is None  # type: ignore[attr-defined]


def test_start_and_end_dates_are_kept() -> None:
    transition = walk(
        state(WizardStep.SCHEDULE_KIND),
        [
            (WizardEvent.SELECT, {"value": "daily"}),
            (WizardEvent.TEXT, {"text": "09:00"}),
            (WizardEvent.SKIP, {}),
            (WizardEvent.TEXT, {"text": "2026-06-01"}),
            (WizardEvent.TEXT, {"text": "2026-06-30"}),
        ],
    )

    assert transition.state.start_date == date(2026, 6, 1)  # type: ignore[attr-defined]
    assert transition.state.end_date == date(2026, 6, 30)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------------------------
# Navigation and terminal states
# --------------------------------------------------------------------------------------------


def test_back_returns_to_the_previous_step_and_keeps_answers() -> None:
    transition = send(
        state(WizardStep.DESCRIPTION, history=(WizardStep.QUESTION,)), WizardEvent.BACK
    )

    assert transition.state.step is WizardStep.QUESTION  # type: ignore[attr-defined]
    assert transition.state.question == "Ship it?"  # type: ignore[attr-defined]
    assert transition.state.history == ()  # type: ignore[attr-defined]


def test_back_at_the_first_step_is_a_no_op() -> None:
    transition = send(state(WizardStep.QUESTION), WizardEvent.BACK)

    assert transition.state.step is WizardStep.QUESTION  # type: ignore[attr-defined]
    assert transition.effect is WizardEffect.PROMPT  # type: ignore[attr-defined]


@pytest.mark.parametrize("event", [WizardEvent.CANCEL, WizardEvent.TIMEOUT])
def test_cancel_and_timeout_end_the_wizard(event: WizardEvent) -> None:
    transition = send(state(WizardStep.OPTIONS), event)

    assert transition.effect is WizardEffect.CANCELLED  # type: ignore[attr-defined]
    assert transition.state.step is WizardStep.CANCELLED  # type: ignore[attr-defined]


@pytest.mark.parametrize("event", list(WizardEvent))
def test_terminal_states_ignore_every_event(event: WizardEvent) -> None:
    transition = send(state(WizardStep.COMPLETED), event)

    assert transition.effect is WizardEffect.IGNORED  # type: ignore[attr-defined]


# --------------------------------------------------------------------------------------------
# Invalid input keeps the step and the answers
# --------------------------------------------------------------------------------------------


def test_a_question_over_the_limit_is_rejected_with_the_length() -> None:
    transition = text(state(WizardStep.QUESTION), "q" * 301)

    assert transition.state.step is WizardStep.QUESTION  # type: ignore[attr-defined]
    assert "300" in (transition.state.error or "")  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("step", "payload", "fragment"),
    [
        (WizardStep.QUESTION, {"text": None}, UNDERSTOOD),
        (WizardStep.DESCRIPTION, {"text": None}, UNDERSTOOD),
        (WizardStep.OPTIONS, {"text": None}, UNDERSTOOD),
        (WizardStep.OPTIONS, {"text": "yes"}, "unique"),
        (WizardStep.SCHEDULE_WEEKDAYS, {"text": "x"}, "weekdays"),
        (WizardStep.SCHEDULE_WEEKDAYS, {"text": "8"}, "7"),
        (WizardStep.SCHEDULE_DAY, {"text": "x"}, "1 to 31"),
        (WizardStep.SCHEDULE_DAY, {"text": "32"}, "31"),
        (WizardStep.SCHEDULE_TIME, {"text": "nine"}, "HH:MM"),
        (WizardStep.SCHEDULE_RUN_AT, {"text": "soon"}, "YYYY-MM-DD HH:MM"),
        (WizardStep.SCHEDULE_TIMEZONE, {"text": "Mars/Base"}, "IANA"),
        (WizardStep.SCHEDULE_START, {"text": "someday"}, "YYYY-MM-DD"),
        (WizardStep.SCHEDULE_END, {"text": "someday"}, "YYYY-MM-DD"),
    ],
)
def test_invalid_text_keeps_the_step_and_reports_the_constraint(
    step: WizardStep, payload: dict[str, object], fragment: str
) -> None:
    transition = send(state(step), WizardEvent.TEXT, **payload)

    assert transition.state.step is step  # type: ignore[attr-defined]
    assert fragment in (transition.state.error or "")  # type: ignore[attr-defined]


def test_options_are_capped_at_twelve() -> None:
    full = tuple(f"Option {index}" for index in range(12))

    transition = text(state(WizardStep.OPTIONS, options=full), "One more")

    assert "12 options" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_options_need_two_before_continuing() -> None:
    transition = send(state(WizardStep.OPTIONS, options=("Only",)), WizardEvent.OPTION_DONE)

    assert "two options" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_a_duplicate_option_differs_only_in_case() -> None:
    transition = text(state(WizardStep.OPTIONS, options=("Yes", "No")), "  yes ")

    assert "unique" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_targets_must_exist_and_be_selected() -> None:
    unknown = send(state(WizardStep.TARGETS), WizardEvent.TOGGLE, value=999)
    empty = send(state(WizardStep.TARGETS, targets=frozenset()), WizardEvent.NEXT)

    assert "not registered" in (unknown.state.error or "")  # type: ignore[attr-defined]
    assert "at least one" in (empty.state.error or "")  # type: ignore[attr-defined]


def test_a_selected_target_is_toggled_off() -> None:
    transition = send(
        state(WizardStep.TARGETS, targets=frozenset({CHAT})), WizardEvent.TOGGLE, value=CHAT
    )

    assert transition.state.targets == frozenset()  # type: ignore[attr-defined]


def test_targets_accept_a_string_id() -> None:
    transition = send(
        state(WizardStep.TARGETS, targets=frozenset()), WizardEvent.TOGGLE, value=str(OTHER_CHAT)
    )

    assert transition.state.targets == frozenset({OTHER_CHAT})  # type: ignore[attr-defined]


def test_an_unknown_schedule_kind_is_rejected() -> None:
    transition = send(state(WizardStep.SCHEDULE_KIND), WizardEvent.SELECT, value="hourly")

    assert transition.state.step is WizardStep.SCHEDULE_KIND  # type: ignore[attr-defined]
    assert "daily" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_selecting_without_a_value_is_rejected() -> None:
    transition = send(state(WizardStep.SCHEDULE_KIND), WizardEvent.SELECT, value=None)

    assert transition.state.error == UNDERSTOOD  # type: ignore[attr-defined]


def test_an_end_before_the_start_is_rejected() -> None:
    transition = send(
        state(
            WizardStep.SCHEDULE_END, schedule_kind=ScheduleKind.DAILY, start_date=date(2026, 6, 2)
        ),
        WizardEvent.TEXT,
        text="2026-06-01",
    )

    assert "end_date" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_a_one_off_without_an_instant_cannot_reach_preview() -> None:
    transition = send(
        state(
            WizardStep.SCHEDULE_END,
            schedule_kind=ScheduleKind.ONCE,
            run_at_local=None,
        ),
        WizardEvent.SKIP,
    )

    assert "one-off" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_end_step_rejects_an_unsupported_event() -> None:
    transition = send(state(WizardStep.SCHEDULE_END), WizardEvent.NEXT)

    assert transition.state.error == UNDERSTOOD  # type: ignore[attr-defined]


def test_preview_requires_confirmation() -> None:
    transition = send(state(WizardStep.PREVIEW), WizardEvent.NEXT)

    assert transition.state.step is WizardStep.PREVIEW  # type: ignore[attr-defined]
    assert "Confirm" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_commit_rejects_an_incomplete_state() -> None:
    transition = send(state(WizardStep.PREVIEW, options=()), WizardEvent.CONFIRM)

    assert transition.state.step is WizardStep.PREVIEW  # type: ignore[attr-defined]
    assert "incomplete" in (transition.state.error or "")  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "action",
    [
        WizardInput(WizardEvent.SKIP),
        WizardInput(WizardEvent.OPTION_DONE),
    ],
)
def test_unsupported_events_are_rejected_on_binary_steps(action: WizardInput) -> None:
    transition = advance(state(WizardStep.ANONYMITY), action, context=CONTEXT)

    assert transition.state.error == UNDERSTOOD


# --------------------------------------------------------------------------------------------
# Exhaustiveness and previews
# --------------------------------------------------------------------------------------------


def test_every_step_and_event_pair_produces_a_transition() -> None:
    for step in WizardStep:
        for event in WizardEvent:
            transition = advance(
                state(step),
                WizardInput(kind=event, text="1", value=CHAT),
                context=CONTEXT,
            )
            assert transition.state.step in WizardStep
            assert transition.effect in WizardEffect


def test_schedule_preview_renders_five_instants() -> None:
    preview = schedule_preview(
        state(
            WizardStep.PREVIEW,
            schedule_kind=ScheduleKind.DAILY,
            time_of_day=time(9, 0),
        ),
        CONTEXT,
    )

    assert len(preview) == 5
    assert preview[0] == "2026-03-02 09:00 UTC"


def test_schedule_preview_is_empty_without_a_schedule() -> None:
    assert schedule_preview(state(WizardStep.PREVIEW, schedule_kind=None), CONTEXT) == ()


def test_option_callback_data_is_a_wizard_key() -> None:
    assert option_keyboard_data(2) == "v1:w:option_remove:2"


# --------------------------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------------------------


def test_the_store_returns_the_current_state() -> None:
    clock = FakeClock(FIXED_NOW)
    store = WizardStore(clock=clock)
    current = state(WizardStep.OPTIONS)

    store.put(current)

    assert store.get(ADMIN) == current
    assert store.get(ADMIN + 1) is None
    assert store.active_count() == 1


def test_a_put_replaces_the_previous_session() -> None:
    store = WizardStore(clock=FakeClock(FIXED_NOW))
    store.put(state(WizardStep.QUESTION))

    store.put(state(WizardStep.OPTIONS))

    assert store.get(ADMIN) is not None
    assert store.get(ADMIN).step is WizardStep.OPTIONS  # type: ignore[union-attr]
    assert store.active_count() == 1


def test_a_session_expires_after_the_ttl() -> None:
    clock = FakeClock(FIXED_NOW)
    store = WizardStore(clock=clock, ttl_seconds=900)
    store.put(state(WizardStep.OPTIONS))

    clock.advance(900)

    assert store.get(ADMIN) is None
    assert store.active_count() == 0


def test_the_janitor_expires_only_stale_sessions() -> None:
    clock = FakeClock(FIXED_NOW)
    store = WizardStore(clock=clock, ttl_seconds=100)
    store.put(WizardState(admin_user_id=1))
    clock.advance(50)
    store.put(WizardState(admin_user_id=2))
    clock.advance(60)

    assert store.expire() == 1
    assert store.active_count() == 1
    assert store.get(2) is not None


def test_discard_is_idempotent() -> None:
    store = WizardStore(clock=FakeClock(FIXED_NOW))
    store.put(state(WizardStep.QUESTION))

    store.discard(ADMIN)
    store.discard(ADMIN)

    assert store.get(ADMIN) is None


def test_terminal_steps_are_only_cancelled_and_completed() -> None:
    assert {WizardStep.CANCELLED, WizardStep.COMPLETED} == TERMINAL_STEPS
    assert WizardState(admin_user_id=1, step=WizardStep.PREVIEW).is_terminal is False


def test_commit_rejects_a_one_off_that_is_now_in_the_past() -> None:
    transition = send(
        state(
            WizardStep.PREVIEW,
            schedule_kind=ScheduleKind.ONCE,
            run_at_local=datetime(2020, 1, 1, 9, 0),
        ),
        WizardEvent.CONFIRM,
    )

    assert transition.state.step is WizardStep.PREVIEW  # type: ignore[attr-defined]
    assert "future" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_a_non_numeric_target_value_is_rejected() -> None:
    transition = send(state(WizardStep.TARGETS), WizardEvent.TOGGLE, value="not-a-chat")

    assert "not registered" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_a_description_over_the_limit_is_rejected() -> None:
    transition = text(state(WizardStep.DESCRIPTION), "d" * 1025)

    assert transition.state.step is WizardStep.DESCRIPTION  # type: ignore[attr-defined]
    assert "1024" in (transition.state.error or "")  # type: ignore[attr-defined]


@pytest.mark.parametrize("option", ["   ", "o" * 101])
def test_an_option_outside_the_length_bounds_is_rejected(option: str) -> None:
    transition = text(state(WizardStep.OPTIONS, options=()), option)

    assert "1-100" in (transition.state.error or "")  # type: ignore[attr-defined]


def test_end_step_rejects_text_without_a_value() -> None:
    transition = send(state(WizardStep.SCHEDULE_END), WizardEvent.TEXT, text=None)

    assert transition.state.error == UNDERSTOOD  # type: ignore[attr-defined]
