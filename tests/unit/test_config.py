"""Configuration parsing tests (SPEC-001 FR-101, constitution §8.1).

The API under test is ``load_settings(env)``; production calls it with ``os.environ``. Every test
supplies the whole environment, so a developer's shell cannot affect the result.
"""

from __future__ import annotations

import pytest

from telegram_auto_poll.config import ConfigurationError, Settings, load_settings
from tests.conftest import FAKE_BOT_TOKEN

MINIMAL_ENV = {
    "TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN,
    "ADMIN_USER_IDS": "100000001",
}


def load(**overrides: str) -> Settings:
    return load_settings({**MINIMAL_ENV, **overrides})


def test_minimal_environment_is_accepted() -> None:
    settings = load()

    assert settings.admin_user_ids == frozenset({100_000_001})
    assert settings.environment == "production"
    assert settings.log_format == "json"
    assert settings.health_port == 8080
    assert settings.default_timezone == "UTC"


def test_defaults_are_applied() -> None:
    settings = load()

    assert settings.scheduler_tick_seconds == 15
    assert settings.send_grace_seconds == 120
    assert settings.send_retry_attempts == 3
    assert settings.send_retry_base_seconds == 2
    assert settings.send_concurrency == 4
    assert settings.admin_commands_per_minute == 20
    assert settings.wizard_ttl_seconds == 900
    assert settings.retention_days == 90
    assert settings.shutdown_grace_seconds == 30
    assert settings.migrate_on_startup is True
    assert settings.build_version == "dev"


def test_multiple_admins_are_parsed_into_a_frozenset() -> None:
    settings = load(ADMIN_USER_IDS="100000001, 100000002 ,100000003")

    assert settings.admin_user_ids == frozenset({100_000_001, 100_000_002, 100_000_003})


def test_missing_token_is_reported_by_name_without_echoing_other_values() -> None:
    env = dict(MINIMAL_ENV)
    del env["TELEGRAM_BOT_TOKEN"]

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env)

    message = str(exc_info.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "required" in message.lower()
    assert "100000001" not in message


def test_empty_admin_allowlist_is_rejected() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(ADMIN_USER_IDS="")

    assert "ADMIN_USER_IDS" in str(exc_info.value)


def test_non_numeric_admin_id_is_rejected() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(ADMIN_USER_IDS="not-a-number")

    message = str(exc_info.value)
    assert "ADMIN_USER_IDS" in message
    assert "not-a-number" not in message


def test_non_positive_admin_id_is_rejected() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(ADMIN_USER_IDS="0")

    assert "ADMIN_USER_IDS" in str(exc_info.value)


@pytest.mark.parametrize("value", ["not-a-port", "0", "70000"])
def test_invalid_health_port_is_rejected(value: str) -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(HEALTH_PORT=value)

    assert "HEALTH_PORT" in str(exc_info.value)


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(DEFAULT_TIMEZONE="Mars/Olympus_Mons")

    message = str(exc_info.value)
    assert "DEFAULT_TIMEZONE" in message
    assert "IANA" in message


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(LOG_LEVEL="LOUD")

    assert "LOG_LEVEL" in str(exc_info.value)


def test_invalid_log_format_is_rejected() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(LOG_FORMAT="xml")

    assert "LOG_FORMAT" in str(exc_info.value)


def test_non_sqlite_database_url_is_rejected() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(DATABASE_URL="postgresql+asyncpg://user@localhost/db")

    assert "DATABASE_URL" in str(exc_info.value)


def test_send_concurrency_must_be_positive() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(SEND_CONCURRENCY="0")

    assert "SEND_CONCURRENCY" in str(exc_info.value)


def test_unknown_variables_are_ignored() -> None:
    settings = load(UNRELATED_VARIABLE="whatever")

    assert settings.health_port == 8080


def test_secret_is_never_in_the_repr_or_the_error() -> None:
    settings = load()

    assert FAKE_BOT_TOKEN not in repr(settings)
    assert FAKE_BOT_TOKEN not in str(settings.model_dump())
    assert settings.telegram_bot_token.get_secret_value() == FAKE_BOT_TOKEN


def test_secret_is_redacted_in_errors_about_other_variables() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(HEALTH_PORT="70000")

    assert FAKE_BOT_TOKEN not in str(exc_info.value)


def test_configuration_error_lists_the_offending_variables() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load(HEALTH_PORT="70000", LOG_LEVEL="LOUD")

    assert set(exc_info.value.variables) >= {"HEALTH_PORT", "LOG_LEVEL"}


def test_admin_ids_may_be_passed_as_a_sequence() -> None:
    settings = Settings(telegram_bot_token=FAKE_BOT_TOKEN, admin_user_ids=[100_000_001])

    assert settings.admin_user_ids == frozenset({100_000_001})


def test_empty_admin_sequence_is_rejected() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        Settings(telegram_bot_token=FAKE_BOT_TOKEN, admin_user_ids=[])


def test_get_settings_reads_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from telegram_auto_poll.config import get_settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("ADMIN_USER_IDS", "100000001")

    assert get_settings().admin_user_ids == frozenset({100_000_001})
