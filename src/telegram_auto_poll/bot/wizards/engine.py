"""Pure sign-up poll wizard engine (SPEC-003 FR-306, FR-307).

``advance(state, input)`` is a total function: every ``(step, event)`` pair yields a transition, and
nothing performs I/O. Validation reuses the domain validators, so the wizard cannot accept a value the
service would reject.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from ...domain.callbacks import wizard_action
from ...domain.errors import ValidationError
from ...domain.validation import OPTIONS_MAX, validate_option_label, validate_poll_title
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
class PollDraft:
    """A fully validated poll, ready for one transaction."""

    title: str
    options: tuple[str, ...]


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
    return replace(
        state,
        step=step,
        error=None,
        history=(*state.history, state.step),
        **changes,  # type: ignore[arg-type]
    )


def _back(state: WizardState) -> Transition:
    if not state.history:
        return _prompt(state)
    return _prompt(replace(state, step=state.history[-1], history=state.history[:-1], error=None))


def _title(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is not WizardEvent.TEXT or action.text is None:
        return _stay(state, UNDERSTOOD)
    try:
        title = validate_poll_title(action.text)
    except ValidationError as exc:
        return _stay(state, str(exc))
    return _prompt(_forward(state, WizardStep.OPTIONS, title=title))


def _options(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is WizardEvent.OPTION_DONE:
        if not state.options:
            return _stay(state, "add at least one option before continuing")
        return _prompt(_forward(state, WizardStep.PREVIEW))
    if action.kind is not WizardEvent.TEXT or action.text is None:
        return _stay(state, UNDERSTOOD)
    if len(state.options) >= OPTIONS_MAX:
        return _stay(state, f"a poll can have at most {OPTIONS_MAX} options")
    try:
        option = validate_option_label(action.text)
    except ValidationError as exc:
        return _stay(state, str(exc))
    if option.casefold() in {existing.casefold() for existing in state.options}:
        return _stay(state, f"options must be unique ('{option}' is repeated)")
    return _prompt(replace(state, options=(*state.options, option), error=None))


def _preview(state: WizardState, action: WizardInput) -> Transition:
    if action.kind is not WizardEvent.CONFIRM:
        return _stay(state, "press Confirm to create the poll, or Back to change something")
    if state.title is None or not state.options:
        return _stay(state, "the poll is incomplete; go back and finish the missing fields")
    return Transition(
        state=replace(state, step=WizardStep.COMPLETED),
        effect=WizardEffect.COMMIT,
        draft=PollDraft(title=state.title, options=state.options),
    )


def advance(state: WizardState, action: WizardInput) -> Transition:
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
    if step is WizardStep.TITLE:
        return _title(state, action)
    if step is WizardStep.OPTIONS:
        return _options(state, action)
    return _preview(state, action)


def remove_option(state: WizardState, index: int) -> WizardState:
    """Remove one option by zero-based index, or return the state with an error."""
    if not 0 <= index < len(state.options):
        return replace(state, error="that option no longer exists")
    return replace(
        state,
        options=tuple(option for position, option in enumerate(state.options) if position != index),
        error=None,
    )


def option_keyboard_data(index: int) -> str:
    """Callback data for removing option ``index`` from the list."""
    return wizard_action("option_remove", index)
