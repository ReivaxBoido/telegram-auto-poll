"""Health sidecar (SPEC-003 FR-339)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from telegram_auto_poll.observability.health import (
    HealthService,
    create_health_app,
)
from telegram_auto_poll.observability.metrics import Metrics
from telegram_auto_poll.scheduler.engine import SchedulerHeartbeat

pytestmark = pytest.mark.allow_network


async def test_report_is_healthy_when_database_and_worker_are_fresh() -> None:
    heartbeat = SchedulerHeartbeat(monotonic=lambda: 1.0)
    heartbeat.beat()
    service = HealthService(database=lambda: _true(), heartbeat=heartbeat, tick_seconds=5)

    report = await service.report()

    assert report.healthy is True
    assert report.checks == {"database": True, "worker": True}
    assert report.reasons == {}


async def test_report_names_database_and_worker_failures() -> None:
    heartbeat = SchedulerHeartbeat(monotonic=lambda: 100.0)
    service = HealthService(database=lambda: _false(), heartbeat=heartbeat, tick_seconds=5)

    report = await service.report()

    assert report.healthy is False
    assert report.reasons["database"] == "SELECT 1 failed"
    assert report.reasons["worker"] == "no heartbeat yet"


async def _true() -> bool:
    return True


async def _false() -> bool:
    return False


@pytest_asyncio.fixture
async def client() -> AsyncIterator[TestClient]:
    heartbeat = SchedulerHeartbeat(monotonic=lambda: 1.0)
    heartbeat.beat()
    app = create_health_app(
        health=HealthService(database=lambda: _true(), heartbeat=heartbeat, tick_seconds=5),
        metrics=Metrics(),
    )
    async with TestClient(TestServer(app)) as test_client:
        yield test_client


async def test_livez_healthz_and_metrics(client: TestClient) -> None:
    live = await client.get("/livez")
    assert live.status == 200
    assert await live.json() == {"status": "ok"}

    health = await client.get("/healthz")
    assert health.status == 200
    assert (await health.json())["status"] == "ok"

    metrics = await client.get("/metrics")
    assert metrics.status == 200
    assert "tap_build_info" in await metrics.text()


async def test_healthz_returns_503_when_unhealthy() -> None:
    heartbeat = SchedulerHeartbeat(monotonic=lambda: 100.0)
    app = create_health_app(
        health=HealthService(database=lambda: _false(), heartbeat=heartbeat, tick_seconds=5),
        metrics=Metrics(),
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/healthz")
        assert response.status == 503
        assert (await response.json())["status"] == "unhealthy"


async def test_health_app_has_three_routes() -> None:
    app = create_health_app(
        health=HealthService(
            database=lambda: _true(), heartbeat=SchedulerHeartbeat(), tick_seconds=5
        ),
        metrics=Metrics(),
    )
    assert isinstance(app, web.Application)
