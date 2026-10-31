"""Prometheus metrics (constitution §16.3, FR-136).

One private registry per process keeps the exposition deterministic, and no metric carries a
high-cardinality label (chat ids, poll ids, or questions never appear).
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

METRIC_NAMES = frozenset(
    {
        "tap_build_info",
        "tap_startup_timestamp_seconds",
        "tap_scheduler_heartbeat_age_seconds",
        "tap_scheduler_tick_duration_seconds",
        "tap_schedules_active",
        "tap_deliveries_pending",
        "tap_polls_sent_total",
        "tap_send_failures_total",
        "tap_send_duration_seconds",
        "tap_schedule_lag_seconds",
        "tap_admin_commands_total",
        "tap_retention_deleted_rows_total",
        "tap_db_up",
        "tap_bot_polling_up",
    }
)

#: Label names that may appear in the exposition. Anything else is a review failure.
ALLOWED_LABELS = frozenset({"version", "sha", "command", "table", "le"})


class Metrics:
    """The application's metric registry and instruments."""

    def __init__(self, *, version: str = "dev", sha: str = "unknown") -> None:
        self.registry = CollectorRegistry()
        self.build_info = Gauge(
            "tap_build_info", "Build information", ["version", "sha"], registry=self.registry
        )
        self.startup_timestamp = Gauge(
            "tap_startup_timestamp_seconds", "Process start time", registry=self.registry
        )
        self.heartbeat_age = Gauge(
            "tap_scheduler_heartbeat_age_seconds",
            "Seconds since the last scheduler tick",
            registry=self.registry,
        )
        self.tick_duration = Histogram(
            "tap_scheduler_tick_duration_seconds",
            "Scheduler tick duration",
            registry=self.registry,
        )
        self.schedules_active = Gauge(
            "tap_schedules_active", "Active schedules", registry=self.registry
        )
        self.deliveries_pending = Gauge(
            "tap_deliveries_pending", "Pending deliveries", registry=self.registry
        )
        self.polls_sent = Counter(
            "tap_polls_sent_total", "Polls sent successfully", registry=self.registry
        )
        self.send_failures = Counter(
            "tap_send_failures_total", "Terminal send failures", registry=self.registry
        )
        self.send_duration = Histogram(
            "tap_send_duration_seconds", "Send duration", registry=self.registry
        )
        self.schedule_lag = Gauge(
            "tap_schedule_lag_seconds",
            "Delay between the occurrence and the send",
            registry=self.registry,
        )
        self.admin_commands = Counter(
            "tap_admin_commands_total",
            "Admin commands by name",
            ["command"],
            registry=self.registry,
        )
        self.retention_deleted = Counter(
            "tap_retention_deleted_rows_total",
            "Rows removed by retention",
            ["table"],
            registry=self.registry,
        )
        self.db_up = Gauge("tap_db_up", "Database reachable", registry=self.registry)
        self.bot_polling_up = Gauge(
            "tap_bot_polling_up", "Telegram polling running", registry=self.registry
        )
        self.build_info.labels(version=version, sha=sha).set(1)

    def mark_started(self, timestamp: float) -> None:
        """Record the process start time."""
        self.startup_timestamp.set(timestamp)

    def render(self) -> bytes:
        """Render the exposition in the Prometheus text format."""
        return generate_latest(self.registry)


__all__ = ["ALLOWED_LABELS", "CONTENT_TYPE_LATEST", "METRIC_NAMES", "Metrics"]
