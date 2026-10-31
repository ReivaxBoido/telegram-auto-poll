"""Health, metrics, and their aiohttp handlers (T-047, FR-136).

Handlers are called directly with a request-like object, so the test needs no listening socket and
the suite keeps NFR-105's "no network" guarantee.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from telegram_auto_poll.observability.health import (
    HEALTH_KEY,
    METRICS_KEY,
    HealthService,
    create_health_app,
    healthz,
    livez,
    metrics_view,
)
from telegram_auto_poll.observability.metrics import ALLOWED_LABELS, METRIC_NAMES, Metrics
from telegram_auto_poll.scheduler.engine import SchedulerHeartbeat


class FakeMonotonic:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def heartbeat_at(value: float) -> tuple[SchedulerHeartbeat, FakeMonotonic]:
    monotonic = FakeMonotonic(value)
    heartbeat = SchedulerHeartbeat(monotonic=monotonic)
    return heartbeat, monotonic


async def database_ok() -> bool:
    return True


async def database_down() -> bool:
    return False


def service(database: object = database_ok, *, beat: bool = True) -> HealthService:
    heartbeat, _ = heartbeat_at(100.0)
    if beat:
        heartbeat.beat()
    return HealthService(
        database=database,  # type: ignore[arg-type]
        heartbeat=heartbeat,
        tick_seconds=15,
    )


def request(app: dict[str, object]) -> object:
    return SimpleNamespace(app=app)


async def test_livez_is_process_only() -> None:
    response = await livez(request({}))

    assert response.status == 200
    assert json.loads(response.body) == {"status": "ok"}


async def test_healthz_is_ready_when_database_and_heartbeat_are_healthy() -> None:
    response = await healthz(request({HEALTH_KEY: service()}))

    body = json.loads(response.body)
    assert response.status == 200
    assert body["status"] == "ok"
    assert body["checks"] == {"database": True, "scheduler": True}
    assert body["reasons"] == {}


async def test_healthz_names_a_database_failure() -> None:
    response = await healthz(request({HEALTH_KEY: service(database_down)}))

    body = json.loads(response.body)
    assert response.status == 503
    assert body["reasons"] == {"database": "SELECT 1 failed"}


async def test_healthz_names_a_missing_heartbeat() -> None:
    response = await healthz(request({HEALTH_KEY: service(beat=False)}))

    body = json.loads(response.body)
    assert response.status == 503
    assert body["reasons"]["scheduler"] == "no heartbeat yet"


async def test_healthz_names_a_stale_heartbeat() -> None:
    heartbeat, monotonic = heartbeat_at(100.0)
    heartbeat.beat()
    monotonic.value += 46
    subject = HealthService(database=database_ok, heartbeat=heartbeat, tick_seconds=15)

    report = await subject.report()

    assert report.healthy is False
    assert report.reasons["scheduler"] == "heartbeat is 46.0s old"


async def test_heartbeat_is_accepted_at_the_tolerance_boundary() -> None:
    heartbeat, monotonic = heartbeat_at(100.0)
    heartbeat.beat()
    monotonic.value += 45
    subject = HealthService(database=database_ok, heartbeat=heartbeat, tick_seconds=15)

    report = await subject.report()

    assert report.healthy is True


async def test_metrics_contain_every_documented_metric() -> None:
    metrics = Metrics(version="0.1.0", sha="abc1234")
    metrics.mark_started(1_772_366_400.0)

    payload = metrics.render().decode()

    for name in METRIC_NAMES:
        assert name in payload, name
    assert 'version="0.1.0"' in payload
    assert 'sha="abc1234"' in payload


def test_metrics_only_use_low_cardinality_labels() -> None:
    metrics = Metrics()
    metrics.admin_commands.labels(command="status").inc()
    metrics.retention_deleted.labels(table="deliveries").inc()

    payload = metrics.render().decode()
    labels = {
        name.split("=")[0].strip()
        for block in re.findall(r"\{([^}]*)\}", payload)
        for name in block.split(",")
    }

    assert labels <= ALLOWED_LABELS


async def test_metrics_view_serves_the_exposition() -> None:
    metrics = Metrics()
    response = await metrics_view(request({METRICS_KEY: metrics}))

    assert response.status == 200
    assert b"tap_build_info" in response.body
    assert "text/plain" in response.headers["Content-Type"]


async def test_the_sidecar_registers_the_three_routes() -> None:
    app = create_health_app(health=service(), metrics=Metrics())

    paths = {
        route.resource.canonical for route in app.router.routes() if route.resource is not None
    }

    assert {"/livez", "/healthz", "/metrics"} <= paths
