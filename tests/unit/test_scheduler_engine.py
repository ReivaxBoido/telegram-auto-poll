"""Scheduler engine and heartbeat (T-033, FR-125, FR-136).

The engine is tested with a fake backend and a fake monotonic clock; the APScheduler adapter is
tested by starting, adding, retiming, removing, and shutting down without waiting for a job to fire.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from telegram_auto_poll.scheduler.engine import (
    SAFETY_TICK_ID,
    ApschedulerBackend,
    SchedulerEngine,
    SchedulerHeartbeat,
)
from telegram_auto_poll.scheduler.planner import PlannedSchedule, ScheduleJobPlanner, job_id_for
from telegram_auto_poll.services.delivery_service import TickOutcome
from tests.unit.test_job_planner import FakeJobScheduler, Loader

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


class FakeBackend:
    def __init__(self, jobs: Mapping[str, datetime] | None = None) -> None:
        self.scheduler = FakeJobScheduler(jobs)
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def shutdown(self) -> None:
        self.stopped += 1

    def jobs(self) -> Mapping[str, datetime]:
        return self.scheduler.jobs()

    def add(self, job_id: str, run_at: datetime) -> None:
        self.scheduler.add(job_id, run_at)

    def remove(self, job_id: str) -> None:
        self.scheduler.remove(job_id)


class FakeMonotonic:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def build(
    *,
    schedules: list[PlannedSchedule] | None = None,
    backend: FakeBackend | None = None,
    monotonic: FakeMonotonic | None = None,
) -> tuple[SchedulerEngine, FakeBackend, Loader, FakeMonotonic]:
    resolved_backend = backend or FakeBackend()
    loader = Loader(schedules or [])
    resolved_monotonic = monotonic or FakeMonotonic()
    planner = ScheduleJobPlanner(scheduler=resolved_backend, load=loader)
    heartbeat = SchedulerHeartbeat(monotonic=resolved_monotonic)

    async def plan() -> TickOutcome:
        return TickOutcome()

    engine = SchedulerEngine(
        planner=planner,
        backend=resolved_backend,
        heartbeat=heartbeat,
        plan=plan,
    )
    return engine, resolved_backend, loader, resolved_monotonic


async def test_start_starts_the_backend_reconciles_and_beats() -> None:
    engine, backend, _, _ = build(schedules=[PlannedSchedule(7, NOW)])

    await engine.start()

    assert backend.started == 1
    assert backend.scheduler.jobs() == {job_id_for(7): NOW}
    assert engine.heartbeat.age() == 0.0


async def test_tick_plans_beats_and_reconciles_advanced_runs() -> None:
    loader_schedules = [PlannedSchedule(7, NOW)]
    engine, backend, loader, monotonic = build(schedules=loader_schedules)
    await engine.start()
    monotonic.value += 20

    async def plan() -> TickOutcome:
        loader.schedules = [PlannedSchedule(7, NOW + timedelta(days=1))]
        return TickOutcome(schedules=1, created=2)

    engine = SchedulerEngine(
        planner=ScheduleJobPlanner(scheduler=backend, load=loader),
        backend=backend,
        heartbeat=engine.heartbeat,
        plan=plan,
    )

    outcome = await engine.tick()

    assert outcome == TickOutcome(schedules=1, created=2)
    assert backend.scheduler.jobs() == {job_id_for(7): NOW + timedelta(days=1)}
    assert engine.heartbeat.age() == 0.0


async def test_tick_with_nothing_due_keeps_the_job_set() -> None:
    engine, backend, _, _ = build(schedules=[PlannedSchedule(7, NOW)])
    await engine.start()

    await engine.tick()

    assert backend.scheduler.removed == []
    assert backend.scheduler.jobs() == {job_id_for(7): NOW}


async def test_shutdown_stops_the_backend() -> None:
    engine, backend, _, _ = build()

    await engine.start()
    await engine.shutdown()

    assert backend.started == 1
    assert backend.stopped == 1


def test_heartbeat_is_not_fresh_before_the_first_beat() -> None:
    heartbeat = SchedulerHeartbeat(monotonic=FakeMonotonic())

    assert heartbeat.age() is None
    assert heartbeat.is_fresh(tolerance_seconds=45) is False


def test_heartbeat_age_tracks_the_monotonic_clock() -> None:
    monotonic = FakeMonotonic()
    heartbeat = SchedulerHeartbeat(monotonic=monotonic)
    heartbeat.beat()

    monotonic.value += 10

    assert heartbeat.age() == 10.0
    assert heartbeat.is_fresh(tolerance_seconds=45) is True
    assert heartbeat.is_fresh(tolerance_seconds=5) is False


def test_heartbeat_never_reports_a_negative_age() -> None:
    monotonic = FakeMonotonic()
    heartbeat = SchedulerHeartbeat(monotonic=monotonic)
    heartbeat.beat()

    monotonic.value -= 5

    assert heartbeat.age() == 0.0


# --------------------------------------------------------------------------------------------
# APScheduler adapter
# --------------------------------------------------------------------------------------------


async def test_backend_starts_with_only_the_safety_tick_registered() -> None:
    ticks: list[int] = []

    async def on_tick() -> None:
        ticks.append(1)

    backend = ApschedulerBackend(tick_seconds=15, on_tick=on_tick)
    backend.start()
    try:
        assert backend.running is True
        assert backend.jobs() == {}
        assert SAFETY_TICK_ID not in backend.jobs()
    finally:
        backend.shutdown()

    # APScheduler flips its state on the next loop turn when shutting down without waiting.
    await asyncio.sleep(0)
    assert backend.running is False


async def test_backend_adds_retimes_and_removes_a_job() -> None:
    async def on_tick() -> None:
        return None

    future = datetime.now(UTC) + timedelta(days=30)
    backend = ApschedulerBackend(tick_seconds=15, on_tick=on_tick)
    backend.start()
    try:
        backend.add(job_id_for(7), future)
        assert backend.jobs()[job_id_for(7)] == future

        backend.add(job_id_for(7), future + timedelta(days=1))
        assert backend.jobs()[job_id_for(7)] == future + timedelta(days=1)

        backend.remove(job_id_for(7))
        assert backend.jobs() == {}
        # Removing an unknown job is a no-op, because reconciliation may race with a firing job.
        backend.remove(job_id_for(7))
    finally:
        backend.shutdown()


async def test_backend_tick_and_due_jobs_call_the_same_pass() -> None:
    ticks: list[int] = []

    async def on_tick() -> None:
        ticks.append(1)

    backend = ApschedulerBackend(tick_seconds=15, on_tick=on_tick)

    await backend._run_tick()
    await backend._run_due()

    assert ticks == [1, 1]


def test_misfire_grace_is_bounded() -> None:
    # A job that fires more than a minute late must not bypass the grace-window check.
    from telegram_auto_poll.scheduler.engine import MISFIRE_GRACE_SECONDS

    assert 0 < MISFIRE_GRACE_SECONDS <= 60
