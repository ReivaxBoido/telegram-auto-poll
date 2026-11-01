"""Sign-up poll wizard state and events (SPEC-003 FR-306, FR-307, FR-316)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WizardStep(StrEnum):
    """Where an admin is in the new-poll wizard."""

    TITLE = "title"
    OPTIONS = "options"
    PREVIEW = "preview"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


TERMINAL_STEPS = frozenset({WizardStep.CANCELLED, WizardStep.COMPLETED})


class WizardEvent(StrEnum):
    """What the admin just did."""

    TEXT = "text"
    OPTION_DONE = "option_done"
    REMOVE_OPTION = "remove_option"
    BACK = "back"
    CANCEL = "cancel"
    CONFIRM = "confirm"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class WizardInput:
    """One event plus its payload."""

    kind: WizardEvent
    text: str | None = None
    value: int | None = None


@dataclass(frozen=True, slots=True)
class WizardState:
    """Everything collected so far, plus navigation history."""

    admin_user_id: int
    step: WizardStep = WizardStep.TITLE
    title: str | None = None
    options: tuple[str, ...] = ()
    history: tuple[WizardStep, ...] = ()
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.step in TERMINAL_STEPS
