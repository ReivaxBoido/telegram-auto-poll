"""Inline keyboards (SPEC-003 §8.6)."""

from __future__ import annotations

import pytest

from telegram_auto_poll.bot.keyboards import (
    KeyboardLayoutError,
    build_keyboard,
    confirmation_keyboard,
    pagination_keyboard,
    poll_detail_keyboard,
    target_keyboard,
    vote_keyboard,
)
from telegram_auto_poll.domain.entities import PollButton, PollRender
from telegram_auto_poll.domain.enums import InstanceStatus, PollStatus
from tests.factories import make_chat, make_instance, make_poll


def _data(keyboard: object) -> list[str]:
    return [
        button.callback_data
        for row in keyboard.inline_keyboard  # type: ignore[attr-defined]
        for button in row
    ]


def test_build_keyboard_validates_layout() -> None:
    markup = build_keyboard([[("A", "v1:v:toggle:1_1")]])
    assert markup.inline_keyboard[0][0].text == "A"

    with pytest.raises(KeyboardLayoutError, match="at most 8 rows"):
        build_keyboard([[("A", "a")]] * 9)
    with pytest.raises(KeyboardLayoutError, match="cannot be empty"):
        build_keyboard([[]])
    with pytest.raises(KeyboardLayoutError, match="at most 4 buttons"):
        build_keyboard([[("A", "a")] * 5])


def test_vote_keyboard_from_render() -> None:
    render = PollRender(
        text="x",
        buttons=(
            PollButton(label="In", callback_data="v1:v:toggle:1_1"),
            PollButton(label="Out", callback_data="v1:v:toggle:1_2"),
        ),
    )
    markup = vote_keyboard(render)
    assert markup is not None
    assert len(markup.inline_keyboard) == 2
    assert all(len(row) == 1 for row in markup.inline_keyboard)
    assert vote_keyboard(PollRender(text="x")) is None


def test_confirmation_and_pagination_callbacks() -> None:
    confirm = confirmation_keyboard(entity="poll", entity_id=12, confirm_action="delete")
    assert _data(confirm) == ["v1:c:delete:12", "v1:w:cancel:poll"]

    paged = pagination_keyboard(
        namespace="p", action="resp", page=2, has_previous=True, has_next=True, argument="12_1"
    )
    assert _data(paged) == ["v1:p:resp:12_1_1", "v1:p:resp:12_1_3"]
    assert (
        pagination_keyboard(
            namespace="p", action="resp", page=1, has_previous=False, has_next=False
        ).inline_keyboard
        == ()
    )


def test_poll_detail_keyboard_reflects_state() -> None:
    draft = poll_detail_keyboard(make_poll(), None)
    assert "Publish" in [button.text for row in draft.inline_keyboard for button in row]

    open_instance = poll_detail_keyboard(make_poll(status=PollStatus.ACTIVE), make_instance())
    labels = [button.text for row in open_instance.inline_keyboard for button in row]
    assert "Close" in labels
    assert "Refresh" in labels
    assert "Responses" in labels

    closed = poll_detail_keyboard(
        make_poll(status=PollStatus.ACTIVE),
        make_instance(status=InstanceStatus.CLOSED),
    )
    assert "Reopen" in [button.text for row in closed.inline_keyboard for button in row]

    deleted = poll_detail_keyboard(make_poll(status=PollStatus.DELETED), None)
    assert deleted.inline_keyboard == ()


def test_target_keyboard_paginates_and_marks_selection() -> None:
    chats = [
        make_chat(id=index + 1, telegram_chat_id=-(index + 1), title=f"Chat {index}")
        for index in range(6)
    ]
    markup = target_keyboard(
        poll_id=12,
        chats=chats,
        selected=frozenset({1}),
        page=1,
        has_previous=False,
        has_next=True,
    )

    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels[0].startswith("[x] Chat 0")
    assert labels[1].startswith("[ ] Chat 1")
    assert "Next" in labels
    assert "Done" in labels
    assert len(markup.inline_keyboard) == 8


def test_target_keyboard_truncates_long_titles() -> None:
    chat = make_chat(id=1, telegram_chat_id=-1, title="x" * 100)
    markup = target_keyboard(
        poll_id=1,
        chats=[chat],
        selected=frozenset(),
        page=1,
        has_previous=True,
        has_next=False,
    )
    assert "…" in markup.inline_keyboard[0][0].text


def test_pagination_without_an_argument() -> None:
    markup = pagination_keyboard(
        namespace="p", action="resp", page=2, has_previous=True, has_next=False
    )
    assert _data(markup) == ["v1:p:resp:1"]


def test_vote_keyboard_allows_one_row_per_option() -> None:
    render = PollRender(
        text="x",
        buttons=tuple(
            PollButton(label=f"Option {index}", callback_data=f"v1:v:toggle:1_{index}")
            for index in range(10)
        ),
    )
    markup = vote_keyboard(render)
    assert markup is not None
    assert len(markup.inline_keyboard) == 10
    assert all(len(row) == 1 for row in markup.inline_keyboard)
