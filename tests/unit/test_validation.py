"""Boundary tests for the pure validators (SPEC-001 FR-117, FR-122; constitution I-1 - I-6)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from telegram_auto_poll.domain.enums import ChatType
from telegram_auto_poll.domain.errors import ValidationError
from telegram_auto_poll.domain.validation import (
    monthly_day_warning,
    validate_chat_type,
    validate_date_range,
    validate_day_of_month,
    validate_description,
    validate_once_instant,
    validate_options,
    validate_question,
    validate_timezone,
    validate_weekdays,
)


def _message(exc_info: pytest.ExceptionInfo[ValidationError]) -> str:
    return str(exc_info.value)


# --------------------------------------------------------------------------------------------
# Question: 1-300
# --------------------------------------------------------------------------------------------


def test_question_at_minimum_length_is_accepted() -> None:
    assert validate_question("Q") == "Q"


def test_question_at_maximum_length_is_accepted() -> None:
    text = "q" * 300
    assert validate_question(text) == text


def test_question_is_trimmed() -> None:
    assert validate_question("  What next?  ") == "What next?"


def test_empty_question_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_question("")
    assert exc_info.value.field == "question"
    assert "300" in _message(exc_info)


def test_whitespace_only_question_is_rejected() -> None:
    with pytest.raises(ValidationError, match="300"):
        validate_question("   \n\t ")


def test_question_over_the_limit_is_rejected_with_the_current_length() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_question("q" * 301)
    message = _message(exc_info)
    assert "300" in message
    assert "301" in message


# --------------------------------------------------------------------------------------------
# Description: 0-1024, optional
# --------------------------------------------------------------------------------------------


def test_missing_description_is_none() -> None:
    assert validate_description(None) is None


def test_blank_description_becomes_none() -> None:
    assert validate_description("   ") is None


def test_description_at_maximum_length_is_accepted() -> None:
    text = "d" * 1024
    assert validate_description(text) == text


def test_description_over_the_limit_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_description("d" * 1025)
    assert exc_info.value.field == "description"
    assert "1024" in _message(exc_info)


# --------------------------------------------------------------------------------------------
# Options: 2-12 unique, 1-100 characters each
# --------------------------------------------------------------------------------------------


def test_two_options_are_accepted() -> None:
    assert validate_options(["Yes", "No"]) == ("Yes", "No")


def test_twelve_options_are_accepted() -> None:
    options = [f"Option {index}" for index in range(12)]
    assert len(validate_options(options)) == 12


def test_one_option_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_options(["Only"])
    assert exc_info.value.field == "options"
    assert "2" in _message(exc_info)


def test_thirteen_options_are_rejected() -> None:
    options = [f"Option {index}" for index in range(13)]
    with pytest.raises(ValidationError, match="12"):
        validate_options(options)


def test_option_at_maximum_length_is_accepted() -> None:
    long_option = "o" * 100
    assert validate_options([long_option, "Other"]) == (long_option, "Other")


def test_option_over_the_limit_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_options(["o" * 101, "Other"])
    assert "100" in _message(exc_info)


def test_blank_option_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_options(["Yes", "   "])
    assert exc_info.value.field == "option"


def test_duplicate_options_differing_only_in_case_and_padding_are_rejected() -> None:
    with pytest.raises(ValidationError, match="unique"):
        validate_options(["Yes", "  yes "])


def test_options_are_trimmed_in_order() -> None:
    assert validate_options(["  A  ", "B "]) == ("A", "B")


# --------------------------------------------------------------------------------------------
# Time zones
# --------------------------------------------------------------------------------------------


def test_utc_timezone_is_accepted() -> None:
    assert validate_timezone("UTC") == "UTC"


def test_iana_timezone_is_accepted() -> None:
    assert validate_timezone("Europe/London") == "Europe/London"


def test_fixed_offset_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError, match="IANA"):
        validate_timezone("+02:00")


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_timezone("Mars/Olympus_Mons")
    assert exc_info.value.field == "timezone"
    assert "IANA" in _message(exc_info)


def test_empty_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_timezone("   ")


# --------------------------------------------------------------------------------------------
# Weekdays: ISO 1-7, distinct
# --------------------------------------------------------------------------------------------


def test_single_weekday_is_accepted() -> None:
    assert validate_weekdays([1]) == (1,)


def test_all_weekdays_are_accepted_and_sorted() -> None:
    assert validate_weekdays([7, 3, 1]) == (1, 3, 7)


def test_weekday_zero_is_rejected() -> None:
    with pytest.raises(ValidationError, match="1"):
        validate_weekdays([0])


def test_weekday_eight_is_rejected() -> None:
    with pytest.raises(ValidationError, match="7"):
        validate_weekdays([8])


def test_empty_weekday_list_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_weekdays([])
    assert exc_info.value.field == "weekdays"


def test_duplicate_weekdays_are_rejected() -> None:
    with pytest.raises(ValidationError, match="unique"):
        validate_weekdays([2, 2])


# --------------------------------------------------------------------------------------------
# Day of month: 1-31
# --------------------------------------------------------------------------------------------


def test_day_one_is_accepted() -> None:
    assert validate_day_of_month(1) == 1


def test_day_thirty_one_is_accepted() -> None:
    assert validate_day_of_month(31) == 31


def test_day_zero_is_rejected() -> None:
    with pytest.raises(ValidationError, match="31"):
        validate_day_of_month(0)


def test_day_thirty_two_is_rejected() -> None:
    with pytest.raises(ValidationError, match="31"):
        validate_day_of_month(32)


def test_days_above_twenty_eight_produce_a_warning() -> None:
    assert monthly_day_warning(29) is not None
    assert monthly_day_warning(31) is not None
    assert monthly_day_warning(28) is None


# --------------------------------------------------------------------------------------------
# Date bounds
# --------------------------------------------------------------------------------------------


def test_open_bounds_are_accepted() -> None:
    assert validate_date_range(None, None) == (None, None)


def test_equal_bounds_are_accepted() -> None:
    day = date(2026, 3, 1)
    assert validate_date_range(day, day) == (day, day)


def test_end_before_start_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_date_range(date(2026, 3, 2), date(2026, 3, 1))
    assert exc_info.value.field == "end_date"


# --------------------------------------------------------------------------------------------
# One-off instants
# --------------------------------------------------------------------------------------------


def test_future_one_off_is_accepted() -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    run_at = now + timedelta(minutes=5)
    assert validate_once_instant(run_at, now=now) == run_at


def test_past_one_off_is_rejected() -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    with pytest.raises(ValidationError) as exc_info:
        validate_once_instant(now - timedelta(seconds=1), now=now)
    assert exc_info.value.field == "run_at"


def test_one_off_equal_to_now_is_rejected() -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    with pytest.raises(ValidationError, match="future"):
        validate_once_instant(now, now=now)


def test_naive_one_off_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        validate_once_instant(
            datetime(2026, 3, 1, 12, 0),
            now=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        )


def test_one_off_is_normalized_to_utc() -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    run_at = datetime(2026, 3, 1, 15, 0, tzinfo=timezone(timedelta(hours=2)))
    assert validate_once_instant(run_at, now=now) == datetime(2026, 3, 1, 13, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------------
# Chat types
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["group", "supergroup", "channel"])
def test_supported_chat_types_are_accepted(value: str) -> None:
    assert validate_chat_type(value) is ChatType(value)


@pytest.mark.parametrize("value", ["private", "bot", ""])
def test_unsupported_chat_types_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_chat_type(value)
    assert exc_info.value.field == "chat_type"


def test_naive_now_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        validate_once_instant(
            datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            now=datetime(2026, 3, 1, 11, 0),
        )
