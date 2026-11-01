"""Scheduler backend and worker heartbeat (SPEC-003 FR-339)."""

from __future__ import annotations

from telegram_auto_poll.scheduler.engine import ApschedulerBackend, SchedulerHeartbeat


def test_heartbeat_ages_and_freshness() -> None:
    now = 10.0
    heartbeat = SchedulerHeartbeat(monotonic=lambda: now)

    assert heartbeat.age() is None
    heartbeat.beat()
    assert heartbeat.age() == 0.0
    assert heartbeat.is_fresh(tolerance_seconds=1) is True

    now = 15.0
    assert heartbeat.age() == 5.0
    assert heartbeat.is_fresh(tolerance_seconds=1) is False


async def test_apscheduler_backend_starts_and_stops() -> None:
    calls: list[str] = []

    async def callback() -> None:
        calls.append("ran")

    backend = ApschedulerBackend()
    backend.add_cron("retention", hour=3, minute=15, callback=callback)
    assert backend.running is False

    backend.start()
    assert backend.running is True
    backend.shutdown()
    await __import__("asyncio").sleep(0)
    assert backend.running is False
    assert calls == []
