"""Pure validators for every user-supplied value (SPEC-001 I-1 - I-6).

Validators normalise as they check (trim, sort weekdays, coerce to UTC) so callers never have to
remember a second rule. A failure always names the field and states the constraint, which is exactly
what the wizard re-prompts with (FR-117).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .enums import ChatType
from .errors import ValidationError

QUESTION_MIN = 1
QUESTION_MAX = 300
DESCRIPTION_MAX = 1024
OPTIONS_MIN = 2
OPTIONS_MAX = 12
OPTION_MIN = 1
OPTION_MAX = 100
WEEKDAY_MIN = 1
WEEKDAY_MAX = 7
DAY_MIN = 1
DAY_MAX = 31
MONTHLY_WARN_ABOVE = 28

_TIMEZONE_HELP = "timezone must be an IANA identifier such as Europe/London or UTC"


def validate_question(value: str) -> str:
    """Return the trimmed question, or raise when it is outside 1-300 characters."""
    text = value.strip()
    if not QUESTION_MIN <= len(text) <= QUESTION_MAX:
        raise ValidationError(
            "question",
            f"question must be {QUESTION_MIN}-{QUESTION_MAX} characters (got {len(text)})",
        )
    return text


def validate_description(value: str | None) -> str | None:
    """Return the trimmed description, ``None`` when blank, or raise above 1024 characters."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > DESCRIPTION_MAX:
        raise ValidationError(
            "description",
            f"description must be 0-{DESCRIPTION_MAX} characters (got {len(text)})",
        )
    return text


def validate_options(options: Sequence[str]) -> tuple[str, ...]:
    """Return trimmed, order-preserving options, enforcing count, length, and uniqueness."""
    if not OPTIONS_MIN <= len(options) <= OPTIONS_MAX:
        raise ValidationError(
            "options",
            f"a poll needs {OPTIONS_MIN}-{OPTIONS_MAX} options (got {len(options)})",
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in options:
        text = raw.strip()
        if not text:
            raise ValidationError(
                "option", f"an option must be {OPTION_MIN}-{OPTION_MAX} characters (got 0)"
            )
        if len(text) > OPTION_MAX:
            raise ValidationError(
                "option",
                f"an option must be {OPTION_MIN}-{OPTION_MAX} characters (got {len(text)})",
            )
        key = text.casefold()
        if key in seen:
            raise ValidationError("option", f"options must be unique ('{text}' is repeated)")
        seen.add(key)
        cleaned.append(text)
    return tuple(cleaned)


def validate_timezone(value: str) -> str:
    """Return the IANA identifier, or raise when the platform tz database does not know it."""
    text = value.strip()
    if not text:
        raise ValidationError("timezone", _TIMEZONE_HELP)
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValidationError("timezone", f"{_TIMEZONE_HELP} (got '{text}')") from exc
    return text


def validate_weekdays(days: Sequence[int]) -> tuple[int, ...]:
    """Return sorted, distinct ISO weekdays (1 = Monday … 7 = Sunday)."""
    if not days:
        raise ValidationError(
            "weekdays",
            f"weekly schedules need at least one weekday ({WEEKDAY_MIN}=Monday .. {WEEKDAY_MAX}=Sunday)",
        )
    seen: set[int] = set()
    for day in days:
        if not WEEKDAY_MIN <= day <= WEEKDAY_MAX:
            raise ValidationError(
                "weekdays",
                f"weekdays must be between {WEEKDAY_MIN} and {WEEKDAY_MAX} (got {day})",
            )
        if day in seen:
            raise ValidationError("weekdays", f"weekdays must be unique ({day} is repeated)")
        seen.add(day)
    return tuple(sorted(seen))


def validate_day_of_month(day: int) -> int:
    """Return the day of month, or raise outside 1-31."""
    if not DAY_MIN <= day <= DAY_MAX:
        raise ValidationError(
            "day_of_month",
            f"day_of_month must be between {DAY_MIN} and {DAY_MAX} (got {day})",
        )
    return day


def monthly_day_warning(day: int) -> str | None:
    """Return the advisory shown for days that do not exist in every month."""
    if day > MONTHLY_WARN_ABOVE:
        return (
            f"day {day} does not exist in every month; "
            "months without it are skipped rather than clamped"
        )
    return None


def validate_date_range(start: date | None, end: date | None) -> tuple[date | None, date | None]:
    """Return the bounds unchanged, or raise when ``end`` precedes ``start``."""
    if start is not None and end is not None and end < start:
        raise ValidationError("end_date", "end_date must be on or after start_date")
    return start, end


def validate_once_instant(run_at: datetime, *, now: datetime) -> datetime:
    """Return the instant coerced to UTC, or raise when it is naive or not in the future."""
    if run_at.tzinfo is None or run_at.utcoffset() is None:
        raise ValidationError("run_at", "run_at must be a timezone-aware datetime")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    moment = run_at.astimezone(UTC)
    if moment <= now.astimezone(UTC):
        raise ValidationError("run_at", "a one-off schedule must be in the future")
    return moment


def validate_chat_type(value: str) -> ChatType:
    """Return the chat type, or raise for private chats, bots, and unknown values."""
    try:
        return ChatType(value.strip().lower())
    except ValueError as exc:
        raise ValidationError(
            "chat_type",
            "only groups, supergroups, and channels can be registered",
        ) from exc
