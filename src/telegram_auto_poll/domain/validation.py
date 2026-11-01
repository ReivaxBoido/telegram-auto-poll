"""Pure validators for every user-supplied value (SPEC-003 I-16, I-17, I-27).

Validators normalise as they check (trim, case-fold for uniqueness) so callers never have to remember
a second rule. A failure always names the field and states the constraint, which is what the wizard
re-prompts with (FR-307).
"""

from __future__ import annotations

from collections.abc import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .enums import ChatType
from .errors import ValidationError

POLL_TITLE_MIN = 1
POLL_TITLE_MAX = 300
OPTIONS_MIN = 1
OPTIONS_MAX = 10
OPTION_MIN = 1
OPTION_MAX = 100
FIRST_NAME_MAX = 64
FALLBACK_FIRST_NAME = "Anonymous"

_TIMEZONE_HELP = "timezone must be an IANA identifier such as Europe/London or UTC"


def validate_poll_title(value: str) -> str:
    """Return the trimmed poll title, or raise outside 1-300 characters."""
    text = value.strip()
    if not POLL_TITLE_MIN <= len(text) <= POLL_TITLE_MAX:
        raise ValidationError(
            "title",
            f"title must be {POLL_TITLE_MIN}-{POLL_TITLE_MAX} characters (got {len(text)})",
        )
    return text


def validate_option_label(value: str) -> str:
    """Return the trimmed option label, or raise outside 1-100 characters."""
    text = value.strip()
    if not OPTION_MIN <= len(text) <= OPTION_MAX:
        raise ValidationError(
            "option",
            f"an option must be {OPTION_MIN}-{OPTION_MAX} characters (got {len(text)})",
        )
    return text


def validate_poll_options(options: Sequence[str]) -> tuple[str, ...]:
    """Return trimmed, order-preserving options, enforcing count, length, and uniqueness."""
    if not OPTIONS_MIN <= len(options) <= OPTIONS_MAX:
        raise ValidationError(
            "options",
            f"a poll needs {OPTIONS_MIN}-{OPTIONS_MAX} options (got {len(options)})",
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in options:
        text = validate_option_label(raw)
        key = text.casefold()
        if key in seen:
            raise ValidationError("option", f"options must be unique ('{text}' is repeated)")
        seen.add(key)
        cleaned.append(text)
    return tuple(cleaned)


def validate_first_name(value: str | None) -> str:
    """Return a safe first-name snapshot.

    Telegram requires a first name, but a profile can be incomplete or unusually long. A blank value
    becomes ``Anonymous`` and a long value is truncated, because refusing a vote over a display name
    would be worse than showing a shortened one.
    """
    text = (value or "").strip()
    if not text:
        return FALLBACK_FIRST_NAME
    return text[:FIRST_NAME_MAX]


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


def validate_chat_type(value: str) -> ChatType:
    """Return the chat type, or raise for private chats, bots, and unknown values."""
    try:
        return ChatType(value.strip().lower())
    except ValueError as exc:
        raise ValidationError(
            "chat_type",
            "only groups, supergroups, and channels can be registered",
        ) from exc
