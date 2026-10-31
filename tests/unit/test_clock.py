"""Clock and sleeper ports (SPEC-001 T-010, constitution: no ``datetime.now()`` outside the port)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from telegram_auto_poll.clock import AsyncioSleeper, SystemClock
from tests.fakes import FakeClock, FakeSleeper


def test_system_clock_returns_aware_utc() -> None:
    moment = SystemClock().now()

    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(0)


def test_system_clock_moves_forward() -> None:
    clock = SystemClock()
    first = clock.now()
    second = clock.now()

    assert second >= first


def test_system_clock_is_close_to_wall_time() -> None:
    moment = SystemClock().now()
    wall = datetime.now(UTC)

    assert abs(wall - moment) < timedelta(seconds=5)


def test_fake_clock_starts_at_the_given_instant() -> None:
    start = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    clock = FakeClock(start)

    assert clock.now() == start


def test_fake_clock_advances() -> None:
    clock = FakeClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))

    clock.advance(90)

    assert clock.now() == datetime(2026, 5, 1, 8, 1, 30, tzinfo=UTC)


def test_fake_clock_rejects_naive_instants() -> None:
    with pytest.raises(ValueError, match="aware"):
        FakeClock(datetime(2026, 5, 1, 8, 0))

    clock = FakeClock()
    with pytest.raises(ValueError, match="aware"):
        clock.set(datetime(2026, 5, 1, 8, 0))


def test_fake_clock_can_be_reset() -> None:
    clock = FakeClock()
    target = datetime(2027, 1, 1, 0, 0, tzinfo=UTC)

    clock.set(target)

    assert clock.now() == target


async def test_fake_sleeper_records_waits_without_sleeping() -> None:
    sleeper = FakeSleeper()

    await sleeper.sleep(1.5)
    await sleeper.sleep(0.25)

    assert sleeper.waits == [1.5, 0.25]


async def test_fake_sleeper_can_advance_a_clock() -> None:
    clock = FakeClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))
    sleeper = FakeSleeper(clock=clock)

    await sleeper.sleep(30)

    assert clock.now() == datetime(2026, 5, 1, 8, 0, 30, tzinfo=UTC)


async def test_asyncio_sleeper_returns() -> None:
    await AsyncioSleeper().sleep(0)
