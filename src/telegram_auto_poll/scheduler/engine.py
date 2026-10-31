"""Scheduler engine: jobs for precision, a safety tick for correctness, and a heartbeat.

A ``DateTrigger`` job wakes the process at the exact next run; an interval tick catches anything a
job missed (crash, suspend, re-time). Both call the same idempotent planning pass, so a double wake
cannot double-send: the database's unique index decides.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

from ..services.delivery_service import TickOutcome
from .planner import JOB_PREFIX, ScheduleJobPlanner

SAFETY_TICK_ID = "safety-tick"
RETENTION_JOB_ID = "retention-daily"
RETENTION_HOUR_UTC = 3
RETENTION_MINUTE_UTC = 15

#: A due job that is late by more than this is left to the safety tick, which applies the grace
#: window and records a skip instead of sending.
MISFIRE_GRACE_SECONDS = 60


@dataclass
class SchedulerHeartbeat:
    """Monotonic liveness marker read by ``/healthz`` (clock jumps must not look like a stall)."""

    monotonic: Callable[[], float] = time.monotonic
    last_beat: float | None = field(default=None, init=False)

    def beat(self) -> None:
        self.last_beat = self.monotonic()

    def age(self) -> float | None:
        """Seconds since the last beat, or ``None`` if the scheduler has never ticked."""
        if self.last_beat is None:
            return None
        return max(self.monotonic() - self.last_beat, 0.0)

    def is_fresh(self, *, tolerance_seconds: float) -> bool:
        """Return whether a beat happened within ``tolerance_seconds``."""
        age = self.age()
        return age is not None and age <= tolerance_seconds


class SchedulerBackend(Protocol):
    """A scheduler that can hold date jobs and run one safety tick."""

    def start(self) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        raise NotImplementedError

    def jobs(self) -> Mapping[str, datetime]:
        raise NotImplementedError

    def add(self, job_id: str, run_at: datetime) -> None:
        raise NotImplementedError

    def remove(self, job_id: str) -> None:
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
    """APScheduler 3.x ``AsyncIOScheduler`` adapter.

    Every job calls the same ``on_tick`` coroutine: the due instant is a wake-up, not a unit of work,
    so a coalesced or duplicated wake-up is harmless.
    """

    def __init__(self, *, tick_seconds: int, on_tick: Callable[[], Awaitable[object]]) -> None:
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._tick_seconds = tick_seconds
        self._on_tick = on_tick

    @property
    def running(self) -> bool:
        return bool(self._scheduler.running)

    def start(self) -> None:
        self._scheduler.add_job(
            self._run_tick,
            "interval",
            seconds=self._tick_seconds,
            id=SAFETY_TICK_ID,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def jobs(self) -> Mapping[str, datetime]:
        return {
            job.id: job.next_run_time
            for job in self._scheduler.get_jobs()
            if job.id.startswith(JOB_PREFIX) and job.next_run_time is not None
        }

    def add(self, job_id: str, run_at: datetime) -> None:
        self._scheduler.add_job(
            self._run_due,
            "date",
            run_date=run_at,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )

    def remove(self, job_id: str) -> None:
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)

    def add_cron(
        self,
        job_id: str,
        *,
        hour: int,
        minute: int,
        callback: Callable[[], Awaitable[object]],
    ) -> None:
        """Register a daily UTC cron job outside the date-job set the planner owns."""
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

    async def _run_tick(self) -> None:
        await self._on_tick()

    async def _run_due(self) -> None:
        # A date job carries no work of its own: planning is idempotent and database-driven.
        await self._on_tick()


class SchedulerEngine:
    """Owns the backend, reconciles jobs, and beats the heartbeat."""

    def __init__(
        self,
        *,
        planner: ScheduleJobPlanner,
        backend: SchedulerBackend,
        heartbeat: SchedulerHeartbeat,
        plan: Callable[[], Awaitable[TickOutcome]],
    ) -> None:
        self._planner = planner
        self._backend = backend
        self.heartbeat = heartbeat
        self._plan = plan

    async def start(self) -> None:
        """Start the backend and bring its jobs in line with the database."""
        self._backend.start()
        await self._planner.reconcile()
        self.heartbeat.beat()

    async def tick(self) -> TickOutcome:
        """Plan due work, beat, and reconcile jobs with any advanced next runs."""
        outcome = await self._plan()
        self.heartbeat.beat()
        await self._planner.reconcile()
        return outcome

    async def shutdown(self) -> None:
        """Stop the backend; in-flight ticks finish on their own."""
        self._backend.shutdown()
