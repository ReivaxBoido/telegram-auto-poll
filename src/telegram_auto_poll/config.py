"""Configuration from the environment (SPEC-001 FR-101, constitution §8.1).

Two rules drive this module:

1. Every value is validated before the application starts; a failure names the variable and the
   expected format.
2. The bot token is a ``SecretStr`` and never appears in ``repr``, ``model_dump``, or an error
   message — including the error for an unrelated variable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain.errors import ValidationError
from .domain.validation import validate_timezone

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_FORMATS = ("json", "console")
DATABASE_URL_PREFIX = "sqlite+aiosqlite://"

#: Human-readable expected format per variable, used when a value fails validation.
_HINTS: dict[str, str] = {
    # The key is the variable name; the value is a help string, not a credential.
    "TELEGRAM_BOT_TOKEN": "required; the BotFather credential, never logged or committed",  # nosec B105
    "ADMIN_USER_IDS": "required; comma-separated positive numeric Telegram user ids",
    "DATABASE_URL": f"required; a {DATABASE_URL_PREFIX} URL",
    "DATA_DIR": "required; a writable absolute directory",
    "DEFAULT_TIMEZONE": "an IANA identifier such as Europe/London or UTC",
    "LOG_LEVEL": ", ".join(LOG_LEVELS),
    "LOG_FORMAT": ", ".join(LOG_FORMATS),
    "HEALTH_PORT": "an integer between 1 and 65535",
    "HEALTH_HOST": "an IP address or hostname the health server binds to",
    "SCHEDULER_TICK_SECONDS": "an integer of at least 1",
    "SEND_GRACE_SECONDS": "an integer of at least 0",
    "SEND_RETRY_ATTEMPTS": "an integer of at least 1",
    "SEND_CONCURRENCY": "an integer of at least 1",
    "WIZARD_TTL_SECONDS": "an integer of at least 1",
    "RETENTION_DAYS": "an integer of at least 1",
}


class ConfigurationError(Exception):
    """One or more environment variables are missing or malformed."""

    def __init__(self, problems: Sequence[tuple[str, str]]) -> None:
        self.problems = tuple(problems)
        self.variables = tuple(name for name, _ in problems)
        detail = "; ".join(f"{name}: {reason}" for name, reason in problems)
        super().__init__(f"invalid configuration: {detail}")


class Settings(BaseSettings):
    """Validated application settings. Construct through :func:`load_settings`."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    telegram_bot_token: SecretStr
    admin_user_ids: frozenset[int]

    database_url: str = "sqlite+aiosqlite:////data/telegram-auto-poll.db"
    data_dir: Path = Path("/data")

    default_timezone: str = "UTC"
    environment: str = "production"
    log_level: str = "INFO"
    log_format: str = "json"

    health_host: str = "127.0.0.1"
    health_port: int = Field(default=8080, ge=1, le=65535)
    metrics_enabled: bool = True

    scheduler_tick_seconds: int = Field(default=15, ge=1)
    send_grace_seconds: int = Field(default=120, ge=0)
    send_retry_attempts: int = Field(default=3, ge=1)
    send_retry_base_seconds: float = Field(default=2.0, gt=0)
    send_concurrency: int = Field(default=4, ge=1)
    bot_poll_timeout_seconds: int = Field(default=30, ge=1)
    clock_skew_warning_seconds: int = Field(default=60, ge=0)

    admin_commands_per_minute: int = Field(default=20, ge=1)
    wizard_ttl_seconds: int = Field(default=900, ge=1)

    retention_days: int = Field(default=90, ge=1)
    db_busy_timeout_ms: int = Field(default=5000, ge=0)
    shutdown_grace_seconds: int = Field(default=30, ge=0)
    migrate_on_startup: bool = True

    build_version: str = "dev"
    build_sha: str = "unknown"

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            if not parts:
                raise ValueError("at least one numeric user id is required")
            return parts
        return value

    @field_validator("admin_user_ids")
    @classmethod
    def _require_positive_admin_ids(cls, value: frozenset[int]) -> frozenset[int]:
        if not value:
            raise ValueError("at least one numeric user id is required")
        if any(user_id <= 0 for user_id in value):
            raise ValueError("user ids must be positive integers")
        return value

    @field_validator("default_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            return validate_timezone(value)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"must be one of {', '.join(LOG_LEVELS)}")
        return level

    @field_validator("log_format")
    @classmethod
    def _check_log_format(cls, value: str) -> str:
        fmt = value.strip().lower()
        if fmt not in LOG_FORMATS:
            raise ValueError(f"must be one of {', '.join(LOG_FORMATS)}")
        return fmt

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        if not value.startswith(DATABASE_URL_PREFIX):
            raise ValueError(f"must be a {DATABASE_URL_PREFIX} URL")
        return value


def _problems(error: PydanticValidationError) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []
    for detail in error.errors(include_url=False, include_input=False, include_context=False):
        location = detail["loc"]
        name = str(location[0]).upper() if location else "configuration"
        problems.append((name, _HINTS.get(name, str(detail["msg"]))))
    return problems


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Validate ``env`` (default: the process environment) into :class:`Settings`."""
    source: Mapping[str, str] = os.environ if env is None else env
    values = {key.lower(): value for key, value in source.items()}
    try:
        return Settings.model_validate(values)
    except PydanticValidationError as exc:
        raise ConfigurationError(_problems(exc)) from exc


def get_settings() -> Settings:
    """Load settings from the process environment; called once at startup."""
    return load_settings()
