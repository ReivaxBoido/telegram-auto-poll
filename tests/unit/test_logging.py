"""JSON logging and redaction (T-047, FR-137)."""

from __future__ import annotations

import json
import logging

from telegram_auto_poll.logging_setup import (
    CONSOLE_FORMAT,
    JsonFormatter,
    configure_logging,
    redact,
)

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def record(message: str, *, level: int = logging.INFO, **extra: object) -> logging.LogRecord:
    created = logging.LogRecord(
        name="telegram_auto_poll.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    created.created = 1_772_366_400.0
    for key, value in extra.items():
        setattr(created, key, value)
    return created


def test_redact_removes_token_shaped_text() -> None:
    assert TOKEN not in redact(f"failed for {TOKEN}")
    assert "[redacted]" in redact(f"failed for {TOKEN}")
    assert redact("nothing secret") == "nothing secret"


def test_json_lines_carry_every_required_field() -> None:
    formatter = JsonFormatter(version="0.1.0", environment="production")

    payload = json.loads(formatter.format(record("startup_complete")))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "telegram_auto_poll.test"
    assert payload["message"] == "startup_complete"
    assert payload["service"] == "telegram-auto-poll"
    assert payload["version"] == "0.1.0"
    assert payload["environment"] == "production"
    assert payload["timestamp"].startswith("2026-03-01T12:00:00")


def test_json_lines_include_scalar_extras() -> None:
    formatter = JsonFormatter(version="dev", environment="test")

    payload = json.loads(formatter.format(record("delivery_sent", delivery_id=7, ok=True)))

    assert payload["delivery_id"] == 7
    assert payload["ok"] is True


def test_the_message_and_extras_are_redacted() -> None:
    formatter = JsonFormatter(version="dev", environment="test")

    payload = json.loads(formatter.format(record(f"token {TOKEN}", note=TOKEN)))

    assert TOKEN not in payload["message"]
    assert TOKEN not in payload["note"]


def test_exceptions_are_rendered_and_redacted() -> None:
    formatter = JsonFormatter(version="dev", environment="test")
    try:
        raise RuntimeError(TOKEN)
    except RuntimeError:
        import sys

        failed = record("boom")
        failed.exc_info = sys.exc_info()

    payload = json.loads(formatter.format(failed))

    assert "RuntimeError" in payload["exception"]
    assert TOKEN not in payload["exception"]


def test_configure_logging_installs_one_json_handler() -> None:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        configure_logging(level="DEBUG", log_format="json", environment="test", version="9")

        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)


def test_configure_logging_installs_the_console_format() -> None:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    try:
        configure_logging(level="warning", log_format="console")

        assert root.level == logging.WARNING
        assert root.handlers[0].formatter is not None
        assert root.handlers[0].formatter._fmt == CONSOLE_FORMAT
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)


def test_underscore_extras_are_not_serialised() -> None:
    formatter = JsonFormatter(version="dev", environment="test")

    payload = json.loads(formatter.format(record("hello", _private="nope")))

    assert "_private" not in payload


def test_non_scalar_extras_are_not_serialised() -> None:
    formatter = JsonFormatter(version="dev", environment="test")

    payload = json.loads(formatter.format(record("hello", nested={"a": 1})))

    assert "nested" not in payload
