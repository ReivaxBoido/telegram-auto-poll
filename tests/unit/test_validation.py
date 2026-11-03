"""Domain validation boundaries (SPEC-003 I-16, I-17, I-27)."""

from __future__ import annotations

import pytest

from telegram_auto_poll.domain.enums import ChatType
from telegram_auto_poll.domain.errors import ValidationError
from telegram_auto_poll.domain.validation import (
    FALLBACK_FIRST_NAME,
    validate_chat_type,
    validate_first_name,
    validate_option_label,
    validate_poll_options,
    validate_poll_title,
    validate_timezone,
)


@pytest.mark.parametrize("title", ["a", "x" * 300])
def test_poll_title_accepts_the_boundaries(title: str) -> None:
    assert validate_poll_title(f"  {title}  ") == title


@pytest.mark.parametrize("title", ["", "   ", "x" * 301])
def test_poll_title_rejects_outside_the_boundaries(title: str) -> None:
    with pytest.raises(ValidationError, match="title must be 1-300"):
        validate_poll_title(title)


@pytest.mark.parametrize("label", ["a", "x" * 100])
def test_option_label_accepts_the_boundaries(label: str) -> None:
    assert validate_option_label(f" {label} ") == label


@pytest.mark.parametrize("label", ["", "x" * 101])
def test_option_label_rejects_outside_the_boundaries(label: str) -> None:
    with pytest.raises(ValidationError, match="option must be 1-100"):
        validate_option_label(label)


def test_poll_options_accepts_one_to_ten_unique_labels() -> None:
    assert validate_poll_options(["In"]) == ("In",)
    labels = tuple(f"Option {index}" for index in range(10))
    assert validate_poll_options(labels) == labels


def test_poll_options_rejects_zero_or_eleven() -> None:
    with pytest.raises(ValidationError, match="a poll needs 1-10 options"):
        validate_poll_options([])
    with pytest.raises(ValidationError, match="a poll needs 1-10 options"):
        validate_poll_options([f"Option {index}" for index in range(11)])


def test_poll_options_rejects_duplicates_case_insensitively() -> None:
    with pytest.raises(ValidationError, match="options must be unique"):
        validate_poll_options(["In", " in "])


def test_first_name_falls_back_and_truncates() -> None:
    assert validate_first_name(None) == FALLBACK_FIRST_NAME
    assert validate_first_name("   ") == FALLBACK_FIRST_NAME
    assert validate_first_name("x" * 80) == "x" * 64
    assert validate_first_name("  Alice  ") == "Alice"


def test_timezone_accepts_iana_and_rejects_unknown() -> None:
    assert validate_timezone(" Europe/London ") == "Europe/London"
    with pytest.raises(ValidationError, match="IANA"):
        validate_timezone("")
    with pytest.raises(ValidationError, match="got 'Mars/Olympus'"):
        validate_timezone("Mars/Olympus")


def test_chat_type_accepts_supported_types_and_rejects_private() -> None:
    assert validate_chat_type("SUPERGROUP") is ChatType.SUPERGROUP
    with pytest.raises(ValidationError, match="only groups"):
        validate_chat_type("private")
