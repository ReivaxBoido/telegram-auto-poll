"""Storage mapping helpers (``db/repositories/common.py``).

These conversions are the only place where the database representation and the domain
representation meet, so the round trips and the naive-datetime refusal are tested directly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from telegram_auto_poll.db.repositories.common import (
    chat_type,
    dumps,
    format_date,
    format_time,
    from_epoch,
    load_int_list,
    load_object,
    load_str_list,
    parse_date,
    parse_time,
    to_epoch,
)
from telegram_auto_poll.domain.enums import ChatType


def test_to_epoch_normalizes_to_utc() -> None:
    moment = datetime(2026, 3, 1, 14, 0, tzinfo=timezone(timedelta(hours=2)))

    assert to_epoch(moment) == int(datetime(2026, 3, 1, 12, 0, tzinfo=UTC).timestamp())


def test_to_epoch_of_none_is_none() -> None:
    assert to_epoch(None) is None


def test_to_epoch_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_epoch(datetime(2026, 3, 1, 12, 0))


def test_from_epoch_returns_aware_utc() -> None:
    moment = from_epoch(1_772_366_400)

    assert moment is not None
    assert moment.tzinfo is UTC


def test_from_epoch_of_none_is_none() -> None:
    assert from_epoch(None) is None


def test_datetime_round_trip_is_lossless_to_the_second() -> None:
    moment = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

    assert from_epoch(to_epoch(moment)) == moment


def test_dumps_is_deterministic_and_compact() -> None:
    assert dumps(["b", "a"]) == '["b","a"]'
    assert dumps({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_json_list_loaders() -> None:
    assert load_str_list('["a","b"]') == ["a", "b"]
    assert load_int_list("[3,1]") == [3, 1]


def test_load_object_of_none_is_none() -> None:
    assert load_object(None) is None


def test_load_object_returns_a_mapping() -> None:
    assert load_object('{"a":1}') == {"a": 1}


def test_time_round_trip() -> None:
    assert format_time(time(9, 5)) == "09:05"
    assert parse_time("09:05") == time(9, 5)
    assert format_time(None) is None
    assert parse_time(None) is None


def test_date_round_trip() -> None:
    assert format_date(date(2026, 3, 1)) == "2026-03-01"
    assert parse_date("2026-03-01") == date(2026, 3, 1)
    assert format_date(None) is None
    assert parse_date(None) is None


def test_chat_type_parses_the_stored_string() -> None:
    assert chat_type("channel") is ChatType.CHANNEL
