"""Rate limiter behaviour (FR-130): fake time only, never a real wait."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from telegram_auto_poll.services.rate_limiter import GLOBAL, PER_CHAT, PER_GROUP, Limit, RateLimiter
from tests.fakes import FakeClock, FakeSleeper


def build(**overrides: Limit) -> tuple[RateLimiter, FakeClock, FakeSleeper]:
    clock = FakeClock(datetime(2026, 3, 1, 12, 0, tzinfo=UTC))
    sleeper = FakeSleeper(clock=clock)
    limiter = RateLimiter(clock=clock, sleeper=sleeper, **overrides)  # type: ignore[arg-type]
    return limiter, clock, sleeper


def test_the_documented_budgets_are_one_per_second_twenty_per_minute_and_twenty_five_per_second() -> (
    None
):
    assert Limit(count=1, window_seconds=1.0) == PER_CHAT
    assert Limit(count=20, window_seconds=60.0) == PER_GROUP
    assert Limit(count=25, window_seconds=1.0) == GLOBAL


async def test_the_first_send_never_waits() -> None:
    limiter, _, sleeper = build()

    await limiter.acquire(-100)

    assert sleeper.waits == []
    assert limiter.tracked(-100) == 1


async def test_three_sends_to_one_chat_are_spaced_by_a_second() -> None:
    limiter, clock, sleeper = build()
    start = clock.now()

    for _ in range(3):
        await limiter.acquire(-100)

    assert sleeper.waits == [1.0, 1.0]
    assert (clock.now() - start).total_seconds() == pytest.approx(2.0, abs=0.001)


async def test_sends_that_are_already_spaced_do_not_wait() -> None:
    limiter, clock, sleeper = build()
    await limiter.acquire(-100)

    clock.advance(1)
    await limiter.acquire(-100)

    assert sleeper.waits == []


async def test_the_per_group_budget_delays_the_twenty_first_send() -> None:
    limiter, _, sleeper = build()

    for _ in range(21):
        await limiter.acquire(-100)

    # The first send is free; the next nineteen are paced at 1/s, so the twentieth send is stamped
    # 19 s after the first and the twenty-first must wait the remaining 41 s of the minute.
    assert sleeper.waits[:19] == [1.0] * 19
    assert sleeper.waits[19] == pytest.approx(41.0)


async def test_the_global_budget_spans_chats() -> None:
    limiter, _, sleeper = build(
        per_chat=Limit(count=5, window_seconds=1.0),
        per_group=Limit(count=50, window_seconds=60.0),
        global_limit=Limit(count=2, window_seconds=1.0),
    )

    await limiter.acquire(-100)
    await limiter.acquire(-200)
    await limiter.acquire(-300)

    assert sleeper.waits == [1.0]


async def test_waiting_is_delegated_and_the_clock_is_re_read() -> None:
    limiter, clock, sleeper = build()
    await limiter.acquire(-100)

    await limiter.acquire(-100)

    assert sleeper.waits == [1.0]
    assert clock.now() == datetime(2026, 3, 1, 12, 0, 1, tzinfo=UTC)


async def test_timestamps_are_pruned_so_memory_stays_bounded() -> None:
    limiter, clock, _ = build()

    for _ in range(3):
        await limiter.acquire(-100)
    assert limiter.tracked(-100) == 3

    clock.advance(61)
    await limiter.acquire(-100)

    assert limiter.tracked(-100) == 1


def test_window_is_the_widest_limit() -> None:
    limiter, _, _ = build()

    assert limiter.window_seconds == 60.0
