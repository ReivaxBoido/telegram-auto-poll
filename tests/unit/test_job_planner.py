"""Job planner behaviour (T-032, constitution §3.4) - fake scheduler, no APScheduler."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from telegram_auto_poll.scheduler.planner import (
    JOB_PREFIX,
    PlannedSchedule,
    ReconcileResult,
    ScheduleJobPlanner,
    job_id_for,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=1)


class FakeJobScheduler:
    def __init__(self, initial: Mapping[str, datetime] | None = None) -> None:
        self._jobs: dict[str, datetime] = dict(initial or {})
        self.added: list[tuple[str, datetime]] = []
        self.removed: list[str] = []

    def jobs(self) -> Mapping[str, datetime]:
        return dict(self._jobs)

    def add(self, job_id: str, run_at: datetime) -> None:
        self.added.append((job_id, run_at))
        self._jobs[job_id] = run_at

    def remove(self, job_id: str) -> None:
        self.removed.append(job_id)
        self._jobs.pop(job_id, None)


class Loader:
    def __init__(self, schedules: Sequence[PlannedSchedule] = ()) -> None:
        self.schedules = list(schedules)
        self.calls = 0

    async def __call__(self) -> Sequence[PlannedSchedule]:
        self.calls += 1
        return list(self.schedules)


def planner(scheduler: FakeJobScheduler, loader: Loader) -> ScheduleJobPlanner:
    return ScheduleJobPlanner(scheduler=scheduler, load=loader)


def test_job_ids_are_namespaced() -> None:
    assert job_id_for(42) == f"{JOB_PREFIX}42"


async def test_reconcile_on_two_empty_sets_does_nothing() -> None:
    scheduler = FakeJobScheduler()
    result = await planner(scheduler, Loader()).reconcile()

    assert result == ReconcileResult()
    assert scheduler.added == []
    assert scheduler.removed == []


async def test_missing_jobs_are_added() -> None:
    scheduler = FakeJobScheduler()
    loader = Loader([PlannedSchedule(schedule_id=7, next_run_at=NOW)])

    result = await planner(scheduler, loader).reconcile()

    assert result == ReconcileResult(added=1, desired=1)
    assert scheduler.jobs() == {job_id_for(7): NOW}
    assert loader.calls == 1


async def test_reconcile_is_idempotent() -> None:
    scheduler = FakeJobScheduler()
    loader = Loader([PlannedSchedule(schedule_id=7, next_run_at=NOW)])
    subject = planner(scheduler, loader)
    await subject.reconcile()

    second = await subject.reconcile()

    assert second == ReconcileResult(desired=1)
    assert len(scheduler.added) == 1
    assert scheduler.removed == []


async def test_a_changed_run_time_is_retimed() -> None:
    scheduler = FakeJobScheduler({job_id_for(7): NOW})
    loader = Loader([PlannedSchedule(schedule_id=7, next_run_at=LATER)])

    result = await planner(scheduler, loader).reconcile()

    assert result == ReconcileResult(retimed=1, desired=1)
    assert scheduler.removed == [job_id_for(7)]
    assert scheduler.jobs() == {job_id_for(7): LATER}


async def test_paused_or_deleted_schedules_lose_their_jobs() -> None:
    scheduler = FakeJobScheduler({job_id_for(7): NOW, job_id_for(8): NOW, job_id_for(9): NOW})
    loader = Loader([PlannedSchedule(schedule_id=7, next_run_at=NOW)])

    result = await planner(scheduler, loader).reconcile()

    assert result == ReconcileResult(removed=2, desired=1)
    assert set(scheduler.removed) == {job_id_for(8), job_id_for(9)}
    assert scheduler.jobs() == {job_id_for(7): NOW}


async def test_unknown_jobs_are_removed() -> None:
    # The planner owns the whole job set it sees; the backend filters out its own safety tick before
    # handing the job list over.
    scheduler = FakeJobScheduler({"safety-tick": NOW})

    result = await planner(scheduler, Loader()).reconcile()

    assert result.removed == 1
    assert scheduler.removed == ["safety-tick"]


async def test_mixed_add_retime_and_remove() -> None:
    scheduler = FakeJobScheduler({job_id_for(1): NOW, job_id_for(2): NOW})
    loader = Loader(
        [
            PlannedSchedule(schedule_id=1, next_run_at=NOW),
            PlannedSchedule(schedule_id=3, next_run_at=LATER),
        ]
    )

    result = await planner(scheduler, loader).reconcile()

    assert result == ReconcileResult(added=1, removed=1, desired=2)
    assert scheduler.jobs() == {job_id_for(1): NOW, job_id_for(3): LATER}


async def test_one_thousand_schedules_are_reconciled_quickly() -> None:
    scheduler = FakeJobScheduler()
    schedules = [
        PlannedSchedule(schedule_id=index, next_run_at=NOW + timedelta(minutes=index))
        for index in range(1_000)
    ]
    loader = Loader(schedules)

    started = time.perf_counter()
    result = await planner(scheduler, loader).reconcile()
    elapsed = time.perf_counter() - started

    assert result == ReconcileResult(added=1_000, desired=1_000)
    assert len(scheduler.jobs()) == 1_000
    assert elapsed < 5.0
