"""Structured logging with central redaction (FR-137, §16.1).

One JSON object per line, always with ``timestamp``, ``level``, ``logger``, ``message``, ``service``,
``version``, and ``environment``. Token-shaped secrets are redacted before serialisation, so a
leaked exception message cannot put a bot token in the logs.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

SERVICE_NAME = "telegram-auto-poll"

_TOKEN_PATTERN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_REDACTED = "[redacted]"

CONSOLE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


def redact(text: str) -> str:
    """Replace BotFather-token-shaped substrings with ``[redacted]``."""
    return _TOKEN_PATTERN.sub(_REDACTED, text)


class JsonFormatter(logging.Formatter):
    """Render one log record as a single JSON object."""

    def __init__(self, *, version: str, environment: str) -> None:
        super().__init__()
        self._version = version
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "service": SERVICE_NAME,
            "version": self._version,
            "environment": self._environment,
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if isinstance(value, str | int | float | bool) or value is None:
                payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info is not None:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def configure_logging(
    *,
    level: str = "INFO",
    log_format: str = "json",
    environment: str = "production",
    version: str = "dev",
) -> None:
    """Install one root handler using the JSON or console format."""
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter(version=version, environment=environment))
    else:
        handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
