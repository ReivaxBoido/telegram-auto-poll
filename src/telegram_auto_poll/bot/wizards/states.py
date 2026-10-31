"""Wizard state and events (FR-111, FR-116, FR-121, FR-122).

The state is a frozen value: the engine returns a new one on every transition, so the store only has
to replace a reference and tests can assert exact states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum

from ...domain.enums import ScheduleKind


class WizardStep(StrEnum):
    """Where an admin is in the new-poll wizard."""

    QUESTION = "question"
    DESCRIPTION = "description"
    OPTIONS = "options"
    ANONYMITY = "anonymity"
    MULTIPLE = "multiple"
    TARGETS = "targets"
    SCHEDULE_KIND = "schedule_kind"
    SCHEDULE_WEEKDAYS = "schedule_weekdays"
    SCHEDULE_DAY = "schedule_day"
    SCHEDULE_TIME = "schedule_time"
    SCHEDULE_RUN_AT = "schedule_run_at"
    SCHEDULE_TIMEZONE = "schedule_timezone"
    SCHEDULE_START = "schedule_start"
    SCHEDULE_END = "schedule_end"
    PREVIEW = "preview"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


TERMINAL_STEPS = frozenset({WizardStep.CANCELLED, WizardStep.COMPLETED})


class WizardEvent(StrEnum):
    """What the admin just did."""

    TEXT = "text"
    SKIP = "skip"
    OPTION_DONE = "option_done"
    TOGGLE = "toggle"
    NEXT = "next"
    SELECT = "select"
    BACK = "back"
    CANCEL = "cancel"
    CONFIRM = "confirm"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class WizardInput:
    """One event plus its payload."""

    kind: WizardEvent
    text: str | None = None
    value: str | int | None = None


@dataclass(frozen=True, slots=True)
class WizardState:
    """Everything collected so far, plus navigation history."""

    admin_user_id: int
    step: WizardStep = WizardStep.QUESTION
    question: str | None = None
    description: str | None = None
    options: tuple[str, ...] = ()
    is_anonymous: bool = True
    allows_multiple_answers: bool = False
    targets: frozenset[int] = field(default_factory=frozenset)
    schedule_kind: ScheduleKind | None = None
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None
    time_of_day: time | None = None
    run_at_local: datetime | None = None
    timezone: str = "UTC"
    start_date: date | None = None
    end_date: date | None = None
    history: tuple[WizardStep, ...] = ()
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.step in TERMINAL_STEPS
