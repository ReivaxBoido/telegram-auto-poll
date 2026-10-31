"""Pure schedule mathematics (SPEC-001 FR-121, FR-123; constitution I-3 - I-8).

All functions are pure and take the "current" instant as an argument: nothing here reads the clock,
so the DST/month matrix is fully deterministic in tests.

Rules encoded here:

* A local wall time that does not exist (spring forward) resolves to the first valid instant after
  it, and is produced exactly once.
* An ambiguous local wall time (fall back) resolves with ``fold=0``, so it is also produced once.
* A monthly schedule whose day does not exist in a given month skips that month; it never clamps.
* ``start_date`` and ``end_date`` are inclusive local dates.
* A one-off schedule stores an absolute UTC instant.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .enums import ScheduleKind

if TYPE_CHECKING:
    from .entities import Schedule

#: Enough headroom for the longest real gap: 29 February can be eight years apart.
MAX_LOOKAHEAD_DAYS = 366 * 9

#: Upper bound for the half-hour DST shifts (Lord Howe) and the one-hour shifts.
_MAX_SHIFT_MINUTES = 24 * 60


def _require_utc(after: datetime) -> datetime:
    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("after must be a timezone-aware datetime")
    return after.astimezone(UTC)


def _resolve_local(day: date, at_time: time, tz: ZoneInfo) -> datetime | None:
    """Resolve a local wall time to UTC, shifting forward when it does not exist."""
    naive = datetime.combine(day, at_time)
    candidate = naive.replace(tzinfo=tz)
    if candidate.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == naive:
        return candidate.astimezone(UTC)
    for minutes in range(1, _MAX_SHIFT_MINUTES + 1):
        probe = naive + timedelta(minutes=minutes)
        resolved = probe.replace(tzinfo=tz)
        if resolved.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == probe:
            return resolved.astimezone(UTC)
    return None


def _matches(schedule: Schedule, day: date) -> bool:
    if schedule.kind is ScheduleKind.DAILY:
        return True
    if schedule.kind is ScheduleKind.WEEKLY:
        return day.isoweekday() in schedule.weekdays
    return day.day == schedule.day_of_month


def _recurring_time(schedule: Schedule) -> time:
    """Validate a recurring rule and return its wall-clock time."""
    if schedule.kind is ScheduleKind.WEEKLY and not schedule.weekdays:
        raise ValueError("a weekly schedule requires at least one weekday")
    if schedule.kind is ScheduleKind.MONTHLY and schedule.day_of_month is None:
        raise ValueError("a monthly schedule requires day_of_month")
    at_time = schedule.time_of_day
    if at_time is None:
        raise ValueError("a recurring schedule requires time_of_day")
    return at_time


def next_occurrence(schedule: Schedule, after: datetime) -> datetime | None:
    """Return the first occurrence strictly after ``after``, or ``None`` when none remains."""
    after_utc = _require_utc(after)

    if schedule.is_paused:
        return None

    if schedule.kind is ScheduleKind.ONCE:
        if schedule.run_at is None:
            raise ValueError("a one-off schedule requires run_at")
        run_at = _require_utc(schedule.run_at)
        return run_at if run_at > after_utc else None

    at_time = _recurring_time(schedule)

    tz = ZoneInfo(schedule.timezone)
    local_day = after_utc.astimezone(tz).date()
    if schedule.start_date is not None and schedule.start_date > local_day:
        local_day = schedule.start_date

    for _ in range(MAX_LOOKAHEAD_DAYS):
        if schedule.end_date is not None and local_day > schedule.end_date:
            return None
        if _matches(schedule, local_day):
            resolved = _resolve_local(local_day, at_time, tz)
            if resolved is not None and resolved > after_utc:
                return resolved
        local_day += timedelta(days=1)
    return None


def next_occurrences(schedule: Schedule, after: datetime, count: int) -> list[datetime]:
    """Return up to ``count`` ordered, distinct occurrences after ``after``."""
    occurrences: list[datetime] = []
    cursor = _require_utc(after)
    for _ in range(max(count, 0)):
        occurrence = next_occurrence(schedule, cursor)
        if occurrence is None:
            break
        occurrences.append(occurrence)
        cursor = occurrence
    return occurrences
