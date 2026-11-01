"""APScheduler wiring for the daily retention job and the worker heartbeat.

Due-schedule evaluation returns in SPEC-004. SPEC-003 keeps only the cron backend and a liveness
marker read by ``/healthz``; the posting worker beats it on every tick.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC
from typing import Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

RETENTION_JOB_ID = "retention-daily"
RETENTION_HOUR_UTC = 3
RETENTION_MINUTE_UTC = 15


@dataclass
class SchedulerHeartbeat:
    """Monotonic liveness marker; clock jumps must not look like a stall."""

    monotonic: Callable[[], float] = time.monotonic
    last_beat: float | None = field(default=None, init=False)

    def beat(self) -> None:
        self.last_beat = self.monotonic()

    def age(self) -> float | None:
        """Seconds since the last beat, or ``None`` if the worker has never ticked."""
        if self.last_beat is None:
            return None
        return max(self.monotonic() - self.last_beat, 0.0)

    def is_fresh(self, *, tolerance_seconds: float) -> bool:
        """Return whether a beat happened within ``tolerance_seconds``."""
        age = self.age()
        return age is not None and age <= tolerance_seconds


class CronBackend(Protocol):
    """A scheduler that can hold one daily cron job."""

    def start(self) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        raise NotImplementedError

    def add_cron(
        self,
        job_id: str,
        *,
        hour: int,
        minute: int,
        callback: Callable[[], Awaitable[object]],
    ) -> None:
        raise NotImplementedError


class ApschedulerBackend:
    """APScheduler 3.x ``AsyncIOScheduler`` adapter for the retention cron."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone=UTC)

    @property
    def running(self) -> bool:
        return bool(self._scheduler.running)

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def add_cron(
        self,
        job_id: str,
        *,
        hour: int,
        minute: int,
        callback: Callable[[], Awaitable[object]],
    ) -> None:
        """Register a daily UTC cron job."""
        self._scheduler.add_job(
            callback,
            "cron",
            hour=hour,
            minute=minute,
            timezone=UTC,
            id=job_id,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
