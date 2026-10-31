"""Pure schedule-math tests (SPEC-001 FR-121, FR-123; constitution I-3 - I-8).

Every expectation is a fixed instant. The DST facts were verified against the platform tz database
with a read-only stdlib script and are quoted in the test names for the reviewer.
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from telegram_auto_poll.domain.enums import ScheduleKind
from telegram_auto_poll.domain.schedule_math import next_occurrence, next_occurrences
from tests.factories import make_schedule


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# Daily
# --------------------------------------------------------------------------------------------


def test_daily_returns_next_occurrence_after_the_given_instant() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY, time_of_day=time(9, 0))

    assert next_occurrence(schedule, after=utc(2026, 3, 1, 8, 59)) == utc(2026, 3, 1, 9, 0)


def test_daily_is_strictly_after_the_given_instant() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY, time_of_day=time(9, 0))

    assert next_occurrence(schedule, after=utc(2026, 3, 1, 9, 0)) == utc(2026, 3, 2, 9, 0)


def test_daily_uses_the_schedule_timezone_not_the_caller() -> None:
    schedule = make_schedule(
        kind=ScheduleKind.DAILY,
        timezone="America/New_York",
        time_of_day=time(9, 0),
    )

    # 2026-03-01 is EST (UTC-5) because US DST starts on 2026-03-08.
    assert next_occurrence(schedule, after=utc(2026, 3, 1, 0, 0)) == utc(2026, 3, 1, 14, 0)


def test_daily_accepts_a_non_utc_after_value() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY, time_of_day=time(9, 0))
    after = datetime(2026, 3, 1, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))

    assert next_occurrence(schedule, after=after) == utc(2026, 3, 2, 9, 0)


def test_daily_crosses_the_year_boundary() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY, time_of_day=time(9, 0))

    assert next_occurrence(schedule, after=utc(2026, 12, 31, 12, 0)) == utc(2027, 1, 1, 9, 0)


def test_naive_after_is_rejected() -> None:
    schedule = make_schedule()

    with pytest.raises(ValueError, match="timezone-aware"):
        next_occurrence(schedule, after=datetime(2026, 3, 1, 0, 0))


# --------------------------------------------------------------------------------------------
# DST — the highest-risk logic (plan.md §11)
# --------------------------------------------------------------------------------------------


def test_spring_forward_nonexistent_time_shifts_forward() -> None:
    # Europe/London jumps 01:00 GMT -> 02:00 BST on 2026-03-29, so 01:30 does not exist.
    # The first valid instant is 02:00+01:00 == 01:00Z, and it is sent exactly once.
    schedule = make_schedule(
        kind=ScheduleKind.DAILY,
        timezone="Europe/London",
        time_of_day=time(1, 30),
    )

    occurrences = next_occurrences(schedule, after=utc(2026, 3, 28, 12, 0), count=2)

    assert occurrences[0] == utc(2026, 3, 29, 1, 0)
    assert occurrences[1] == utc(2026, 3, 30, 0, 30)


def test_fall_back_ambiguous_time_sends_once() -> None:
    # Europe/London repeats 01:00-02:00 on 2026-10-25. The first (fold=0) instant,
    # 01:30+01:00 == 00:30Z, is used, and the next occurrence is a full day later.
    schedule = make_schedule(
        kind=ScheduleKind.DAILY,
        timezone="Europe/London",
        time_of_day=time(1, 30),
    )

    occurrences = next_occurrences(schedule, after=utc(2026, 10, 24, 12, 0), count=2)

    assert occurrences[0] == utc(2026, 10, 25, 0, 30)
    assert occurrences[1] == utc(2026, 10, 26, 1, 30)


def test_lord_howe_half_hour_dst_shift() -> None:
    # Lord Howe Island shifts by 30 minutes: 02:00 -> 02:30 on 2026-10-04, so 02:15 is
    # nonexistent and resolves to 02:30+11:00 == 2026-10-03T15:30Z.
    schedule = make_schedule(
        kind=ScheduleKind.DAILY,
        timezone="Australia/Lord_Howe",
        time_of_day=time(2, 15),
    )

    assert next_occurrence(schedule, after=utc(2026, 10, 3, 0, 0)) == utc(2026, 10, 3, 15, 30)


def test_lord_howe_ambiguous_half_hour_uses_fold_zero() -> None:
    # 2026-04-05 01:45 is ambiguous after the 30-minute fall-back; fold=0 is 14:45Z on 04-04.
    schedule = make_schedule(
        kind=ScheduleKind.DAILY,
        timezone="Australia/Lord_Howe",
        time_of_day=time(1, 45),
    )

    assert next_occurrence(schedule, after=utc(2026, 4, 4, 0, 0)) == utc(2026, 4, 4, 14, 45)


# --------------------------------------------------------------------------------------------
# Weekly
# --------------------------------------------------------------------------------------------


def test_weekly_multiple_weekdays_are_ordered() -> None:
    schedule = make_schedule(kind=ScheduleKind.WEEKLY, weekdays=(1, 5), time_of_day=time(9, 0))

    # 2026-01-01 is a Thursday, so Friday 01-02 comes before Monday 01-05.
    occurrences = next_occurrences(schedule, after=utc(2026, 1, 1, 0, 0), count=3)

    assert occurrences == [utc(2026, 1, 2, 9, 0), utc(2026, 1, 5, 9, 0), utc(2026, 1, 9, 9, 0)]


def test_weekly_crosses_a_month_boundary() -> None:
    schedule = make_schedule(kind=ScheduleKind.WEEKLY, weekdays=(1, 3), time_of_day=time(9, 0))

    # 2026-01-28 is a Wednesday; the next slot is Monday 2026-02-02.
    assert next_occurrence(schedule, after=utc(2026, 1, 28, 12, 0)) == utc(2026, 2, 2, 9, 0)


def test_weekly_sunday_is_iso_weekday_seven() -> None:
    schedule = make_schedule(kind=ScheduleKind.WEEKLY, weekdays=(7,), time_of_day=time(9, 0))

    assert next_occurrence(schedule, after=utc(2026, 1, 9, 0, 0)) == utc(2026, 1, 11, 9, 0)


def test_weekly_without_weekdays_is_a_programming_error() -> None:
    schedule = make_schedule(kind=ScheduleKind.WEEKLY, weekdays=())

    with pytest.raises(ValueError, match="weekday"):
        next_occurrence(schedule, after=utc(2026, 1, 1, 0, 0))


# --------------------------------------------------------------------------------------------
# Monthly
# --------------------------------------------------------------------------------------------


def test_monthly_missing_day_is_skipped() -> None:
    # 31 January -> February has no 31st -> March 31. Never clamped to 28 February.
    schedule = make_schedule(
        kind=ScheduleKind.MONTHLY,
        day_of_month=31,
        time_of_day=time(9, 0),
    )

    occurrences = next_occurrences(schedule, after=utc(2026, 1, 31, 12, 0), count=2)

    assert occurrences == [utc(2026, 3, 31, 9, 0), utc(2026, 5, 31, 9, 0)]


def test_monthly_feb_29() -> None:
    schedule = make_schedule(
        kind=ScheduleKind.MONTHLY,
        day_of_month=29,
        time_of_day=time(0, 0),
    )

    # 2026 is not a leap year: February is skipped.
    assert next_occurrence(schedule, after=utc(2026, 1, 29, 0, 0)) == utc(2026, 3, 29, 0, 0)
    # 2028 is: 29 February exists.
    leap = next_occurrences(schedule, after=utc(2028, 1, 29, 0, 0), count=2)
    assert leap == [utc(2028, 2, 29, 0, 0), utc(2028, 3, 29, 0, 0)]


def test_monthly_day_one_hits_every_month() -> None:
    schedule = make_schedule(
        kind=ScheduleKind.MONTHLY,
        day_of_month=1,
        time_of_day=time(12, 0),
    )

    occurrences = next_occurrences(schedule, after=utc(2026, 1, 1, 12, 0), count=2)

    assert occurrences == [utc(2026, 2, 1, 12, 0), utc(2026, 3, 1, 12, 0)]


def test_monthly_without_day_of_month_is_a_programming_error() -> None:
    schedule = make_schedule(kind=ScheduleKind.MONTHLY, day_of_month=None)

    with pytest.raises(ValueError, match="day_of_month"):
        next_occurrence(schedule, after=utc(2026, 1, 1, 0, 0))


# --------------------------------------------------------------------------------------------
# One-off
# --------------------------------------------------------------------------------------------


def test_once_converts_the_local_instant_to_utc() -> None:
    run_at = datetime(2026, 6, 1, 18, 30, tzinfo=timezone(timedelta(hours=2)))
    schedule = make_schedule(kind=ScheduleKind.ONCE, time_of_day=None, run_at=run_at)

    assert next_occurrence(schedule, after=utc(2026, 6, 1, 0, 0)) == utc(2026, 6, 1, 16, 30)


def test_once_returns_none_once_the_instant_has_passed() -> None:
    schedule = make_schedule(
        kind=ScheduleKind.ONCE,
        time_of_day=None,
        run_at=utc(2026, 6, 1, 16, 30),
    )

    assert next_occurrence(schedule, after=utc(2026, 6, 1, 16, 30)) is None
    assert next_occurrence(schedule, after=utc(2026, 6, 1, 16, 31)) is None


def test_once_without_run_at_is_a_programming_error() -> None:
    schedule = make_schedule(kind=ScheduleKind.ONCE, time_of_day=None, run_at=None)

    with pytest.raises(ValueError, match="run_at"):
        next_occurrence(schedule, after=utc(2026, 1, 1, 0, 0))


# --------------------------------------------------------------------------------------------
# Bounds and pause state
# --------------------------------------------------------------------------------------------


def test_start_date_is_inclusive() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY, start_date=date(2026, 3, 10))

    assert next_occurrence(schedule, after=utc(2026, 3, 1, 0, 0)) == utc(2026, 3, 10, 9, 0)


def test_end_date_is_inclusive() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY, end_date=date(2026, 3, 5))

    assert next_occurrence(schedule, after=utc(2026, 3, 4, 12, 0)) == utc(2026, 3, 5, 9, 0)
    assert next_occurrence(schedule, after=utc(2026, 3, 5, 9, 0)) is None


def test_expired_schedule_returns_none() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY, end_date=date(2025, 12, 31))

    assert next_occurrence(schedule, after=utc(2026, 1, 1, 0, 0)) is None


def test_paused_schedule_returns_none() -> None:
    schedule = make_schedule(is_paused=True)

    assert next_occurrence(schedule, after=utc(2026, 1, 1, 0, 0)) is None


# --------------------------------------------------------------------------------------------
# Sequences
# --------------------------------------------------------------------------------------------


def test_next_occurrences_are_ordered_and_unique() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY)

    occurrences = next_occurrences(schedule, after=utc(2026, 3, 1, 0, 0), count=5)

    assert len(occurrences) == 5
    assert occurrences == sorted(occurrences)
    assert len(set(occurrences)) == 5
    gaps = [later - earlier for earlier, later in itertools.pairwise(occurrences)]
    assert gaps == [timedelta(days=1)] * 4


def test_next_occurrences_with_zero_count_is_empty() -> None:
    schedule = make_schedule()

    assert next_occurrences(schedule, after=utc(2026, 3, 1, 0, 0), count=0) == []


def test_next_occurrences_stops_at_the_end_date() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY, end_date=date(2026, 3, 2))

    occurrences = next_occurrences(schedule, after=utc(2026, 3, 1, 0, 0), count=5)

    assert occurrences == [utc(2026, 3, 1, 9, 0), utc(2026, 3, 2, 9, 0)]


def test_next_run_is_monotonic_across_a_month_boundary() -> None:
    schedule = make_schedule(kind=ScheduleKind.MONTHLY, day_of_month=31)

    occurrences = next_occurrences(schedule, after=utc(2026, 1, 1, 0, 0), count=4)

    assert occurrences == [
        utc(2026, 1, 31, 9, 0),
        utc(2026, 3, 31, 9, 0),
        utc(2026, 5, 31, 9, 0),
        utc(2026, 7, 31, 9, 0),
    ]


# --------------------------------------------------------------------------------------------
# Defensive paths: invalid configurations and the bounded loops
# --------------------------------------------------------------------------------------------


def test_recurring_schedule_without_time_of_day_is_a_programming_error() -> None:
    schedule = make_schedule(kind=ScheduleKind.DAILY, time_of_day=None)

    with pytest.raises(ValueError, match="time_of_day"):
        next_occurrence(schedule, after=utc(2026, 1, 1, 0, 0))


def test_day_is_skipped_when_no_valid_instant_can_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram_auto_poll.domain import schedule_math

    # With the shift window disabled, a nonexistent wall time cannot resolve, so that day is
    # skipped rather than sent at a wrong instant.
    monkeypatch.setattr(schedule_math, "_MAX_SHIFT_MINUTES", 0)
    schedule = make_schedule(
        kind=ScheduleKind.DAILY,
        timezone="Europe/London",
        time_of_day=time(1, 30),
    )

    assert next_occurrence(schedule, after=utc(2026, 3, 29, 0, 0)) == utc(2026, 3, 30, 0, 30)


def test_lookahead_exhaustion_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from telegram_auto_poll.domain import schedule_math

    monkeypatch.setattr(schedule_math, "MAX_LOOKAHEAD_DAYS", 0)

    assert next_occurrence(make_schedule(), after=utc(2026, 1, 1, 0, 0)) is None
