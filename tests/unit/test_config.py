"""Configuration loading and validation (SPEC-003 FR-301)."""

from __future__ import annotations

import pytest

from telegram_auto_poll.config import ConfigurationError, load_settings
from tests.conftest import ADMIN_USER_ID, FAKE_BOT_TOKEN


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN,
        "ADMIN_USER_IDS": str(ADMIN_USER_ID),
    }
    base.update(overrides)
    return base


def test_defaults_and_new_settings() -> None:
    settings = load_settings(_env())

    assert settings.response_names_per_option == 50
    assert settings.response_update_debounce_seconds == 1.0
    assert settings.votes_per_minute == 30
    assert settings.response_retention_days == 90
    assert settings.message_edit_concurrency == 4
    assert settings.message_edit_retry_attempts == 3
    assert settings.posting_tick_seconds == 5
    assert settings.allow_destructive_migration is False
    assert settings.default_timezone == "UTC"


def test_new_settings_accept_boundaries() -> None:
    settings = load_settings(
        _env(
            RESPONSE_NAMES_PER_OPTION="50",
            RESPONSE_UPDATE_DEBOUNCE_SECONDS="0",
            VOTES_PER_MINUTE="120",
            RESPONSE_RETENTION_DAYS="3650",
            MESSAGE_EDIT_CONCURRENCY="20",
            MESSAGE_EDIT_RETRY_ATTEMPTS="10",
            POSTING_TICK_SECONDS="60",
            ALLOW_DESTRUCTIVE_MIGRATION="true",
        )
    )
    assert settings.response_names_per_option == 50
    assert settings.response_update_debounce_seconds == 0
    assert settings.allow_destructive_migration is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"RESPONSE_NAMES_PER_OPTION": "0"},
        {"RESPONSE_NAMES_PER_OPTION": "51"},
        {"RESPONSE_UPDATE_DEBOUNCE_SECONDS": "11"},
        {"VOTES_PER_MINUTE": "0"},
        {"RESPONSE_RETENTION_DAYS": "0"},
        {"MESSAGE_EDIT_CONCURRENCY": "0"},
        {"MESSAGE_EDIT_RETRY_ATTEMPTS": "0"},
        {"POSTING_TICK_SECONDS": "0"},
    ],
)
def test_new_settings_reject_out_of_range(overrides: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings(_env(**overrides))
    assert next(iter(overrides)) in str(caught.value)


def test_missing_token_and_empty_admins_are_reported() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings({})
    assert "TELEGRAM_BOT_TOKEN" in str(caught.value)
    assert "ADMIN_USER_IDS" in str(caught.value)

    with pytest.raises(ConfigurationError, match="ADMIN_USER_IDS"):
        load_settings({"TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN, "ADMIN_USER_IDS": ""})


def test_invalid_timezone_log_level_format_and_database_url() -> None:
    with pytest.raises(ConfigurationError, match="DEFAULT_TIMEZONE"):
        load_settings(_env(DEFAULT_TIMEZONE="Mars/Olympus"))
    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        load_settings(_env(LOG_LEVEL="LOUD"))
    with pytest.raises(ConfigurationError, match="LOG_FORMAT"):
        load_settings(_env(LOG_FORMAT="xml"))
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        load_settings(_env(DATABASE_URL="postgres://x"))


def test_token_never_appears_in_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings(_env(LOG_LEVEL="LOUD"))
    assert FAKE_BOT_TOKEN not in str(caught.value)
