"""Prometheus metrics for CountMeIn polls (SPEC-003 §11).

One private registry per process keeps the exposition deterministic. No metric carries a
high-cardinality label: chat ids, poll ids, instance ids, user ids, and names never appear.
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
        "tap_worker_heartbeat_age_seconds",
        "tap_polls_total",
        "tap_poll_instances_open",
        "tap_poll_messages_total",
        "tap_votes_total",
        "tap_vote_rejections_total",
        "tap_message_edits_total",
        "tap_broadcast_coalesced_total",
        "tap_responses_active",
        "tap_vote_to_edit_seconds",
        "tap_response_retention_deleted_total",
        "tap_admin_commands_total",
        "tap_retention_deleted_rows_total",
        "tap_send_duration_seconds",
        "tap_db_up",
        "tap_bot_polling_up",
    }
)

#: Label names that may appear in the exposition. Anything else is a review failure.
ALLOWED_LABELS = frozenset(
    {"version", "sha", "status", "action", "reason", "kind", "command", "table", "le"}
)


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
        self.worker_heartbeat_age = Gauge(
            "tap_worker_heartbeat_age_seconds",
            "Seconds since the last posting-worker tick",
            registry=self.registry,
        )
        self.polls_total = Gauge(
            "tap_polls_total", "Polls by status", ["status"], registry=self.registry
        )
        self.poll_instances_open = Gauge(
            "tap_poll_instances_open", "Open poll instances", registry=self.registry
        )
        self.poll_messages_total = Gauge(
            "tap_poll_messages_total",
            "Poll messages by status",
            ["status"],
            registry=self.registry,
        )
        self.votes_total = Counter(
            "tap_votes_total", "Participant votes", ["action"], registry=self.registry
        )
        self.vote_rejections = Counter(
            "tap_vote_rejections_total",
            "Rejected votes",
            ["reason"],
            registry=self.registry,
        )
        self.message_edits = Counter(
            "tap_message_edits_total",
            "Message edit outcomes",
            ["status"],
            registry=self.registry,
        )
        self.broadcast_coalesced = Counter(
            "tap_broadcast_coalesced_total",
            "Broadcast passes that edited at least one message",
            registry=self.registry,
        )
        self.responses_active = Gauge(
            "tap_responses_active", "Current responses", registry=self.registry
        )
        self.vote_to_edit = Histogram(
            "tap_vote_to_edit_seconds",
            "Seconds from vote to visible edit",
            registry=self.registry,
        )
        self.response_retention_deleted = Counter(
            "tap_response_retention_deleted_total",
            "Responses removed by retention",
            ["kind"],
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
        self.send_duration = Histogram(
            "tap_send_duration_seconds", "Send duration", registry=self.registry
        )
        self.db_up = Gauge("tap_db_up", "Database reachable", registry=self.registry)
        self.bot_polling_up = Gauge(
            "tap_bot_polling_up", "Telegram polling running", registry=self.registry
        )
        self.build_info.labels(version=version, sha=sha).set(1)

    def mark_started(self, timestamp: float) -> None:
        """Record the process start time."""
        self.startup_timestamp.set(timestamp)

    def observe_heartbeat(self, age: float | None) -> None:
        """Publish the worker heartbeat age."""
        self.worker_heartbeat_age.set(0.0 if age is None else age)

    def sent(self) -> None:
        """Count a successful post."""
        self.poll_messages_total.labels(status="sent").inc()
        self.send_duration.observe(0.0)

    def failed(self, reason: str) -> None:
        """Count a terminal post failure."""
        self.poll_messages_total.labels(status="failed").inc()

    def skipped(self) -> None:
        """Count a skipped post."""
        self.poll_messages_total.labels(status="skipped").inc()

    def vote(self, action: str) -> None:
        """Count an accepted vote toggle."""
        self.votes_total.labels(action=action).inc()

    def vote_rejected(self, reason: str) -> None:
        """Count a rejected vote."""
        self.vote_rejections.labels(reason=reason).inc()

    def message_edit(self, status: str) -> None:
        """Count a message edit outcome."""
        self.message_edits.labels(status=status).inc()

    def render(self) -> bytes:
        """Render the exposition in the Prometheus text format."""
        return generate_latest(self.registry)


__all__ = ["ALLOWED_LABELS", "CONTENT_TYPE_LATEST", "METRIC_NAMES", "Metrics"]
