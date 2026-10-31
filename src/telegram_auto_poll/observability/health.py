"""aiohttp sidecar: ``/livez``, ``/healthz``, ``/metrics`` (FR-136, §16.2).

``/livez`` proves the process is running. ``/healthz`` adds the database check and a scheduler
heartbeat younger than three ticks, and names every failing reason in the body. Polling failures are
reported as a metric, never as a readiness failure.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiohttp import web

from ..scheduler.engine import SchedulerHeartbeat
from .metrics import CONTENT_TYPE_LATEST, Metrics

HEARTBEAT_TOLERANCE_TICKS = 3


@dataclass(frozen=True, slots=True)
class HealthReport:
    """The readiness verdict and its reasons."""

    healthy: bool
    checks: dict[str, bool]
    reasons: dict[str, str]


class HealthService:
    """Evaluates the database and scheduler heartbeat."""

    def __init__(
        self,
        *,
        database: Callable[[], Awaitable[bool]],
        heartbeat: SchedulerHeartbeat,
        tick_seconds: int,
    ) -> None:
        self._database = database
        self._heartbeat = heartbeat
        self._tolerance = tick_seconds * HEARTBEAT_TOLERANCE_TICKS

    async def report(self) -> HealthReport:
        db_ok = await self._database()
        age = self._heartbeat.age()
        heartbeat_ok = age is not None and age <= self._tolerance

        reasons: dict[str, str] = {}
        if not db_ok:
            reasons["database"] = "SELECT 1 failed"
        if not heartbeat_ok:
            reasons["scheduler"] = (
                "no heartbeat yet" if age is None else f"heartbeat is {age:.1f}s old"
            )
        return HealthReport(
            healthy=not reasons,
            checks={"database": db_ok, "scheduler": heartbeat_ok},
            reasons=reasons,
        )


HEALTH_KEY: web.AppKey[HealthService] = web.AppKey("health", HealthService)
METRICS_KEY: web.AppKey[Metrics] = web.AppKey("metrics", Metrics)


async def livez(_request: web.Request) -> web.Response:
    """Process-only liveness."""
    return web.json_response({"status": "ok"})


async def healthz(request: web.Request) -> web.Response:
    """Readiness: database plus scheduler heartbeat."""
    report: HealthReport = await request.app[HEALTH_KEY].report()
    return web.json_response(
        {
            "status": "ok" if report.healthy else "unhealthy",
            "checks": report.checks,
            "reasons": report.reasons,
        },
        status=200 if report.healthy else 503,
    )


async def metrics_view(request: web.Request) -> web.Response:
    """Prometheus exposition."""
    metrics: Metrics = request.app[METRICS_KEY]
    return web.Response(body=metrics.render(), headers={"Content-Type": CONTENT_TYPE_LATEST})


def create_health_app(*, health: HealthService, metrics: Metrics) -> web.Application:
    """Build the sidecar application with the three documented routes."""
    app = web.Application()
    app[HEALTH_KEY] = health
    app[METRICS_KEY] = metrics
    app.router.add_get("/livez", livez)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/metrics", metrics_view)
    return app
