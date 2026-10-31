"""Mapping helpers shared by the repositories.

The database stores epoch seconds, JSON text, and ``HH:MM`` strings; the domain uses aware
datetimes, tuples, and ``time`` objects. Every conversion lives here so the two representations
cannot drift.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any, cast, overload

from ...domain.enums import ChatType

if TYPE_CHECKING:
    from sqlalchemy import CursorResult
    from sqlalchemy.engine import Result


# The overloads describe "a datetime in, an int out", so repositories never need a None check after
# passing a required instant. Their bodies are excluded by the constitution §11.3
# ``NotImplementedError`` rule, exactly like the port protocols.
@overload
def to_epoch(moment: datetime) -> int:
    raise NotImplementedError


@overload
def to_epoch(moment: None) -> None:
    raise NotImplementedError


def to_epoch(moment: datetime | None) -> int | None:
    """Convert an aware datetime to UTC epoch seconds."""
    if moment is None:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("refusing to store a naive datetime")
    return int(moment.astimezone(UTC).timestamp())


def rowcount(result: Result[Any]) -> int:
    """Return the affected-row count of a DML statement (typed as ``Result`` by SQLAlchemy)."""
    return int(cast("CursorResult[Any]", result).rowcount or 0)


def from_epoch(value: int | None) -> datetime | None:
    """Convert epoch seconds to an aware UTC datetime."""
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC)


def dumps(value: object) -> str:
    """Serialise a JSON payload deterministically."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def load_str_list(value: str) -> list[str]:
    """Load a JSON array of strings."""
    parsed: Any = json.loads(value)
    return [str(item) for item in parsed]


def load_int_list(value: str) -> list[int]:
    """Load a JSON array of integers."""
    parsed: Any = json.loads(value)
    return [int(item) for item in parsed]


def load_object(value: str | None) -> dict[str, object] | None:
    """Load a nullable JSON object."""
    if value is None:
        return None
    parsed: Any = json.loads(value)
    result: dict[str, object] = parsed
    return result


def format_time(value: time | None) -> str | None:
    """Render ``HH:MM`` for storage."""
    if value is None:
        return None
    return value.strftime("%H:%M")


def parse_time(value: str | None) -> time | None:
    """Parse a stored ``HH:MM`` value."""
    if value is None:
        return None
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def format_date(value: date | None) -> str | None:
    """Render ``YYYY-MM-DD`` for storage."""
    return None if value is None else value.isoformat()


def parse_date(value: str | None) -> date | None:
    """Parse a stored ``YYYY-MM-DD`` value."""
    return None if value is None else date.fromisoformat(value)


def chat_type(value: str) -> ChatType:
    """Parse a stored chat type."""
    return ChatType(value)
