"""Prometheus metrics (SPEC-003 §11)."""

from __future__ import annotations

from telegram_auto_poll.observability.metrics import (
    ALLOWED_LABELS,
    METRIC_NAMES,
    Metrics,
)


def test_metric_catalog_and_exposition() -> None:
    metrics = Metrics(version="0.3.0", sha="abc")
    metrics.mark_started(123.0)
    metrics.observe_heartbeat(None)
    metrics.observe_heartbeat(1.5)
    metrics.sent()
    metrics.failed("bad_request")
    metrics.skipped()
    metrics.vote("added")
    metrics.vote_rejected("closed")
    metrics.message_edit("edited")

    exposition = metrics.render().decode()

    assert "tap_build_info" in exposition
    assert 'version="0.3.0"' in exposition
    assert "tap_poll_messages_total" in exposition
    assert "tap_votes_total" in exposition
    assert "tap_message_edits_total" in exposition
    assert "tap_worker_heartbeat_age_seconds" in exposition


def test_metric_names_and_labels_are_bounded() -> None:
    assert "tap_polls_total" in METRIC_NAMES
    assert "tap_poll_instances_open" in METRIC_NAMES
    assert "tap_vote_to_edit_seconds" in METRIC_NAMES
    assert "chat_id" not in ALLOWED_LABELS
    assert "user_id" not in ALLOWED_LABELS
    assert {"status", "action", "reason", "kind"} <= ALLOWED_LABELS
