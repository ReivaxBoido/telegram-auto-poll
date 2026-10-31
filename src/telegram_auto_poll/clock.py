"""Time adapters.

Production code never calls ``datetime.now()`` or ``asyncio.sleep`` directly; it depends on the
``Clock`` and ``Sleeper`` ports so tests can advance and observe time deterministically.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from .domain.ports import Clock, Sleeper

__all__ = ["AsyncioSleeper", "Clock", "Sleeper", "SystemClock"]


class SystemClock:
    """Wall clock in UTC. The only place in the application that reads the real time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class AsyncioSleeper:
    """Sleeper backed by the event loop."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
