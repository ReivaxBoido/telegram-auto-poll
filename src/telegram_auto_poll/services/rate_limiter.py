"""Telegram pacing (FR-130): one message per second per chat, twenty per minute per group, and
twenty-five per second overall.

The limiter never sleeps on its own: it asks the ``Sleeper`` port, so tests advance a fake clock and
production uses the event loop. Timestamps older than the widest window are pruned on every call,
which keeps memory bounded no matter how many chats exist.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..domain.ports import Clock, Sleeper


@dataclass(frozen=True, slots=True)
class Limit:
    """At most ``count`` events inside ``window_seconds``."""

    count: int
    window_seconds: float


PER_CHAT = Limit(count=1, window_seconds=1.0)
PER_GROUP = Limit(count=20, window_seconds=60.0)
GLOBAL = Limit(count=25, window_seconds=1.0)


class RateLimiter:
    """Sliding-window limiter that waits before a send would exceed a budget."""

    def __init__(
        self,
        *,
        clock: Clock,
        sleeper: Sleeper,
        per_chat: Limit = PER_CHAT,
        per_group: Limit = PER_GROUP,
        global_limit: Limit = GLOBAL,
    ) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self._per_chat = per_chat
        self._per_group = per_group
        self._global_limit = global_limit
        self._chats: dict[int, deque[datetime]] = {}
        self._global: deque[datetime] = deque()

    @property
    def window_seconds(self) -> float:
        """The widest window, which is how long a timestamp must be kept."""
        return max(self._per_group.window_seconds, self._per_chat.window_seconds)

    async def acquire(self, chat_id: int) -> None:
        """Wait until sending to ``chat_id`` is allowed, then record the send."""
        wait = self._required_wait(chat_id, self._clock.now())
        if wait > 0:
            await self._sleeper.sleep(wait)

        now = self._clock.now()
        stamps = self._chats.setdefault(chat_id, deque())
        stamps.append(now)
        self._global.append(now)
        self._prune(stamps, now)
        self._prune(self._global, now)

    def tracked(self, chat_id: int) -> int:
        """Return how many timestamps are retained for one chat (diagnostics and tests)."""
        return len(self._chats.get(chat_id, ()))

    def _required_wait(self, chat_id: int, now: datetime) -> float:
        stamps = self._chats.get(chat_id, deque())
        return max(
            self._wait_for(stamps, self._per_chat, now),
            self._wait_for(stamps, self._per_group, now),
            self._wait_for(self._global, self._global_limit, now),
        )

    def _wait_for(self, stamps: deque[datetime], limit: Limit, now: datetime) -> float:
        recent = [stamp for stamp in stamps if (now - stamp).total_seconds() < limit.window_seconds]
        if len(recent) < limit.count:
            return 0.0
        oldest_counted = recent[len(recent) - limit.count]
        remaining = limit.window_seconds - (now - oldest_counted).total_seconds()
        return max(remaining, 0.0)

    def _prune(self, stamps: deque[datetime], now: datetime) -> None:
        horizon = timedelta(seconds=self.window_seconds)
        while stamps and now - stamps[0] >= horizon:
            stamps.popleft()
