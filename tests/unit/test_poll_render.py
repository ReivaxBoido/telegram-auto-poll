"""Poll message rendering (SPEC-003 FR-326 - FR-328)."""

from __future__ import annotations

import pytest

from telegram_auto_poll.domain.entities import OptionResponses, PollButton, PollRender
from telegram_auto_poll.domain.enums import InstanceStatus, PollStatus
from telegram_auto_poll.domain.poll_render import keyboard_rows, render_poll_message
from tests.factories import make_instance, make_option, make_poll


def _options(*names: str) -> tuple[OptionResponses, ...]:
    first = make_option(0, label="In")
    second = make_option(1, label="Out")
    return (
        OptionResponses(
            option=first, respondents=tuple((index, name) for index, name in enumerate(names))
        ),
        OptionResponses(option=second),
    )


def test_open_poll_renders_counts_names_and_buttons() -> None:
    render = render_poll_message(
        make_poll(status=PollStatus.ACTIVE),
        make_instance(),
        _options("Alice", "Bob"),
        names_per_option=10,
    )

    assert "Saturday football" in render.text
    assert "In (2👥)" in render.text
    assert "Alice" in render.text
    assert "Bob" in render.text
    assert "Out (0👥)" in render.text
    assert "👥 2 people responded" in render.text
    assert [button.callback_data for button in render.buttons] == [
        "v1:v:toggle:100_1",
        "v1:v:toggle:100_2",
    ]


def test_nobody_and_single_respondent_lines() -> None:
    nobody = render_poll_message(
        make_poll(status=PollStatus.ACTIVE), make_instance(), _options(), names_per_option=10
    )
    assert "👥 Nobody responded" in nobody.text

    single = render_poll_message(
        make_poll(status=PollStatus.ACTIVE), make_instance(), _options("Alice"), names_per_option=10
    )
    assert "👥 1 person responded" in single.text


def test_name_limit_and_more_marker() -> None:
    names = tuple(f"Name {index}" for index in range(12))
    render = render_poll_message(
        make_poll(status=PollStatus.ACTIVE), make_instance(), _options(*names), names_per_option=10
    )

    assert "Name 0" in render.text
    assert "Name 9" in render.text
    assert "Name 10" not in render.text
    assert "... +2 more" in render.text


def test_closed_instance_has_no_buttons() -> None:
    render = render_poll_message(
        make_poll(status=PollStatus.ACTIVE),
        make_instance(status=InstanceStatus.CLOSED),
        _options("Alice"),
        names_per_option=10,
    )

    assert "Voting closed." in render.text
    assert render.buttons == ()
    assert render.closed is True


@pytest.mark.parametrize(
    ("poll_status", "instance_status"),
    [(PollStatus.DELETED, InstanceStatus.OPEN), (PollStatus.ACTIVE, InstanceStatus.DELETED)],
)
def test_deleted_poll_or_instance_is_redacted(
    poll_status: PollStatus, instance_status: InstanceStatus
) -> None:
    render = render_poll_message(
        make_poll(status=poll_status),
        make_instance(status=instance_status),
        _options("Alice"),
        names_per_option=10,
    )

    assert render.text == "This poll was deleted."
    assert render.buttons == ()
    assert render.closed is True


def test_long_names_are_reduced_until_the_message_fits() -> None:
    options = tuple(
        OptionResponses(
            option=make_option(index, label=f"Option {index} " + "x" * 80),
            respondents=tuple((user, "Name" + "y" * 60) for user in range(10)),
        )
        for index in range(10)
    )
    render = render_poll_message(
        make_poll(status=PollStatus.ACTIVE, options=tuple(option.option for option in options)),
        make_instance(),
        options,
        names_per_option=10,
        max_length=4_096,
    )

    assert len(render.text) <= 4_096
    assert "Option 0" in render.text
    assert "Option 9" in render.text


def test_zero_name_limit_renders_counts_only() -> None:
    render = render_poll_message(
        make_poll(status=PollStatus.ACTIVE),
        make_instance(),
        _options("Alice"),
        names_per_option=0,
    )
    assert "In (1👥)" in render.text
    assert "Alice" not in render.text


def test_render_requires_a_persisted_instance_and_option() -> None:
    with pytest.raises(ValueError, match="persisted instance id"):
        render_poll_message(
            make_poll(status=PollStatus.ACTIVE),
            make_instance(id=None),
            _options(),
            names_per_option=10,
        )

    unpersisted = OptionResponses(option=make_option(0, id=None))
    with pytest.raises(ValueError, match="persisted option id"):
        render_poll_message(
            make_poll(status=PollStatus.ACTIVE),
            make_instance(),
            (unpersisted,),
            names_per_option=10,
        )


def test_keyboard_rows_groups_two_per_row_and_handles_empty() -> None:
    assert keyboard_rows(PollRender(text="x")) == ()
    buttons = tuple(
        PollButton(label=f"L{index}", callback_data=f"v1:v:1:{index}") for index in range(3)
    )
    rows = keyboard_rows(PollRender(text="x", buttons=buttons))
    assert len(rows) == 2
    assert len(rows[0]) == 2
    assert len(rows[1]) == 1


def test_button_labels_are_truncated() -> None:
    render = render_poll_message(
        make_poll(status=PollStatus.ACTIVE, options=(make_option(0, label="x" * 100),)),
        make_instance(),
        (OptionResponses(option=make_option(0, label="x" * 100)),),
        names_per_option=10,
    )
    assert render.buttons[0].label.endswith("…")
    assert len(render.buttons[0].label) == 60
