"""Domain entities (SPEC-003 §5)."""

from __future__ import annotations

import pytest

from telegram_auto_poll.domain.entities import Chat, Poll, PollInstance, PollMessage, PollOption
from telegram_auto_poll.domain.enums import InstanceStatus, MessageStatus, PollStatus
from telegram_auto_poll.domain.errors import ValidationError
from tests.factories import FIXED_NOW, make_instance, make_message, make_poll


def test_chat_create_normalises_and_validates() -> None:
    chat = Chat.create(
        telegram_chat_id=-1_001_234_567_890,
        chat_type="supergroup",
        title="  Test  ",
        now=FIXED_NOW,
    )
    assert chat.title == "Test"
    assert chat.is_active is True

    with pytest.raises(ValidationError, match="negative"):
        Chat.create(telegram_chat_id=1, chat_type="group", title="x", now=FIXED_NOW)
    with pytest.raises(ValidationError, match="must not be blank"):
        Chat.create(telegram_chat_id=-1_001, chat_type="group", title="  ", now=FIXED_NOW)


def test_poll_create_builds_options_and_finds_them() -> None:
    poll = Poll.create(title="  Game  ", options=["In", "Out"], created_by=1, now=FIXED_NOW)

    assert poll.title == "Game"
    assert [option.label for option in poll.options] == ["In", "Out"]
    assert poll.options[0].position == 0
    assert poll.status is PollStatus.DRAFT
    assert poll.option(999) is None


def test_poll_option_create_validates_label() -> None:
    option = PollOption.create(" In ", position=0)
    assert option.label == "In"
    with pytest.raises(ValidationError, match="option must be 1-100"):
        PollOption.create("", position=0)


def test_instance_open_and_message_terminal_helpers() -> None:
    assert make_instance().is_open is True
    assert make_instance(status=InstanceStatus.CLOSED).is_open is False
    assert make_message().is_terminal is True
    assert make_message(status=MessageStatus.PENDING).is_terminal is False
    assert isinstance(make_poll(), Poll)
    assert isinstance(make_instance(), PollInstance)
    assert isinstance(make_message(), PollMessage)
