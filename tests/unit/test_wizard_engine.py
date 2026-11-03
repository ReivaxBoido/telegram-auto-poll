"""Sign-up poll wizard engine (SPEC-003 FR-306, FR-307)."""

from __future__ import annotations

from telegram_auto_poll.bot.wizards.engine import (
    PollDraft,
    WizardEffect,
    advance,
    option_keyboard_data,
    remove_option,
)
from telegram_auto_poll.bot.wizards.states import WizardEvent, WizardInput, WizardState, WizardStep

ADMIN = 100_000_001


def _state(step: WizardStep = WizardStep.TITLE, **kwargs: object) -> WizardState:
    return WizardState(admin_user_id=ADMIN, step=step, **kwargs)  # type: ignore[arg-type]


def test_title_then_options_then_preview_then_commit() -> None:
    state = _state()
    state = advance(state, WizardInput(WizardEvent.TEXT, text="  Game  ")).state
    assert state.title == "Game"
    assert state.step is WizardStep.OPTIONS

    state = advance(state, WizardInput(WizardEvent.TEXT, text="In")).state
    state = advance(state, WizardInput(WizardEvent.TEXT, text="Out")).state
    assert state.options == ("In", "Out")

    preview = advance(state, WizardInput(WizardEvent.OPTION_DONE))
    assert preview.state.step is WizardStep.PREVIEW

    committed = advance(preview.state, WizardInput(WizardEvent.CONFIRM))
    assert committed.effect is WizardEffect.COMMIT
    assert committed.draft == PollDraft(title="Game", options=("In", "Out"))


def test_invalid_title_and_understood_input_stay() -> None:
    state = _state()
    invalid = advance(state, WizardInput(WizardEvent.TEXT, text=""))
    assert invalid.effect is WizardEffect.PROMPT
    assert "title must be 1-300" in (invalid.state.error or "")

    wrong_event = advance(state, WizardInput(WizardEvent.OPTION_DONE))
    assert "not understood" in (wrong_event.state.error or "")


def test_option_limits_duplicates_and_empty_done() -> None:
    state = _state(WizardStep.OPTIONS, title="Game")
    empty_done = advance(state, WizardInput(WizardEvent.OPTION_DONE))
    assert "at least one" in (empty_done.state.error or "")

    duplicate = advance(
        _state(WizardStep.OPTIONS, title="Game", options=("In",)),
        WizardInput(WizardEvent.TEXT, text=" in "),
    )
    assert "unique" in (duplicate.state.error or "")

    full = _state(
        WizardStep.OPTIONS,
        title="Game",
        options=tuple(f"Option {index}" for index in range(10)),
    )
    over = advance(full, WizardInput(WizardEvent.TEXT, text="Extra"))
    assert "at most 10" in (over.state.error or "")


def test_back_cancel_and_terminal_states() -> None:
    state = _state(WizardStep.OPTIONS, title="Game", options=("In",), history=(WizardStep.TITLE,))
    back = advance(state, WizardInput(WizardEvent.BACK))
    assert back.state.step is WizardStep.TITLE

    cancelled = advance(state, WizardInput(WizardEvent.CANCEL))
    assert cancelled.effect is WizardEffect.CANCELLED
    assert cancelled.state.step is WizardStep.CANCELLED

    ignored = advance(cancelled.state, WizardInput(WizardEvent.TEXT, text="x"))
    assert ignored.effect is WizardEffect.IGNORED


def test_remove_option_and_keyboard_data() -> None:
    state = _state(WizardStep.OPTIONS, title="Game", options=("In", "Out"))
    assert remove_option(state, 0).options == ("Out",)
    assert "no longer exists" in (remove_option(state, 9).error or "")
    assert option_keyboard_data(0) == "v1:w:option_remove:0"


def test_preview_without_title_is_rejected() -> None:
    state = _state(WizardStep.PREVIEW, title=None, options=("In",))
    result = advance(state, WizardInput(WizardEvent.CONFIRM))
    assert "incomplete" in (result.state.error or "")
