"""Architectural conventions for the CountMeIn implementation (SPEC-003 FR-305, FR-340)."""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "telegram_auto_poll"


def _source_files() -> list[Path]:
    return [path for path in SRC.rglob("*.py") if "migrations" not in path.parts]


def test_no_native_poll_delivery_remains() -> None:
    text = "\n".join(path.read_text() for path in _source_files())
    assert "send_poll" not in text
    assert "sendPoll" not in text
    assert "getPollResults" not in text
    assert "stopPoll" not in text


def test_domain_and_services_use_the_clock_port() -> None:
    for path in _source_files():
        if "domain" not in path.parts and "services" not in path.parts:
            continue
        text = path.read_text()
        assert "datetime.now(" not in text, path
        assert "datetime.utcnow(" not in text, path


def test_callback_namespaces_are_the_documented_set() -> None:
    from telegram_auto_poll.domain.callbacks import NAMESPACES

    assert frozenset({"v", "p", "t", "w", "c"}) == NAMESPACES


def test_metric_catalog_has_no_high_cardinality_labels() -> None:
    from telegram_auto_poll.observability.metrics import ALLOWED_LABELS

    assert "chat_id" not in ALLOWED_LABELS
    assert "poll_id" not in ALLOWED_LABELS
    assert "instance_id" not in ALLOWED_LABELS
    assert "user_id" not in ALLOWED_LABELS
