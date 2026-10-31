"""Reconcile in-memory scheduler jobs with the authoritative database (constitution §3.4).

The database decides *what* should run and *when*; this module only makes the scheduler's job set
match it. Reconciliation is idempotent, so it is safe to call at startup, after every schedule
mutation, and after every tick.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

JOB_PREFIX = "schedule:"


def job_id_for(schedule_id: int) -> str:
    """Return the scheduler job id for a schedule."""
    return f"{JOB_PREFIX}{schedule_id}"


@dataclass(frozen=True, slots=True)
class PlannedSchedule:
    """One schedule that should have a job at ``next_run_at``."""

    schedule_id: int
    next_run_at: datetime


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """The changes one reconciliation made."""

    added: int = 0
    removed: int = 0
    retimed: int = 0
    desired: int = 0


class JobScheduler(Protocol):
    """The subset of APScheduler the planner needs."""

    def jobs(self) -> Mapping[str, datetime]:
        raise NotImplementedError

    def add(self, job_id: str, run_at: datetime) -> None:
        raise NotImplementedError

    def remove(self, job_id: str) -> None:
        raise NotImplementedError


#: Loads the schedules that should currently own a job.
PlannedLoader = Callable[[], Awaitable[Sequence[PlannedSchedule]]]


class ScheduleJobPlanner:
    """Keeps the scheduler's job set equal to the database's upcoming runs."""

    def __init__(self, *, scheduler: JobScheduler, load: PlannedLoader) -> None:
        self._scheduler = scheduler
        self._load = load

    async def reconcile(self) -> ReconcileResult:
        """Add, re-time, and remove jobs so they match the database."""
        desired = {
            job_id_for(schedule.schedule_id): schedule.next_run_at
            for schedule in await self._load()
        }
        existing = dict(self._scheduler.jobs())
        added = 0
        removed = 0
        retimed = 0

        for job_id, run_at in desired.items():
            current = existing.pop(job_id, None)
            if current is None:
                self._scheduler.add(job_id, run_at)
                added += 1
            elif current != run_at:
                self._scheduler.remove(job_id)
                self._scheduler.add(job_id, run_at)
                retimed += 1

        for stale in existing:
            self._scheduler.remove(stale)
            removed += 1

        return ReconcileResult(added=added, removed=removed, retimed=retimed, desired=len(desired))
