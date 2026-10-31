"""Rendering and keyboard layout (T-040, FR-120, spec §8.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from telegram_auto_poll.bot.callbacks import decode
from telegram_auto_poll.bot.keyboards import (
    MAX_BUTTONS_PER_ROW,
    MAX_ROWS,
    KeyboardLayoutError,
    build_keyboard,
    confirmation_keyboard,
    pagination_keyboard,
)
from telegram_auto_poll.bot.render import (
    MAX_MESSAGE_LENGTH,
    chat_line,
    empty_listing,
    format_instant,
    page_hint,
    poll_detail,
    poll_line,
    schedule_line,
    schedule_view,
    status_message,
    truncate,
)
from telegram_auto_poll.domain.enums import ScheduleKind
from telegram_auto_poll.services.pagination import Page
from telegram_auto_poll.services.poll_service import PollDetail, ScheduleView
from tests.factories import FIXED_NOW, make_chat, make_poll, make_schedule

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


def test_short_text_is_not_modified() -> None:
    assert truncate("hello") == "hello"


def test_long_text_is_cut_to_the_message_limit() -> None:
    rendered = truncate("x" * 5_000)

    assert len(rendered) == MAX_MESSAGE_LENGTH
    assert rendered.endswith("...")


def test_instants_render_in_utc_minutes() -> None:
    assert format_instant(datetime(2026, 3, 1, 14, 30, tzinfo=UTC)) == "2026-03-01 14:30 UTC"


def test_chat_line_reports_state() -> None:
    assert "active" in chat_line(make_chat())
    assert "inactive" in chat_line(make_chat(is_active=False))


def test_poll_line_reports_counts_and_archive_state() -> None:
    active = poll_line(make_poll())
    archived = poll_line(make_poll(is_archived=True))

    assert "#10" in active
    assert "3 options" in active
    assert "active" in active
    assert "archived" in archived


def test_schedule_line_covers_paused_next_and_missing_runs() -> None:
    assert "paused" in schedule_line(make_schedule(is_paused=True, next_run_at=None))
    assert "2026-03-01 09:00 UTC" in schedule_line(make_schedule(next_run_at=NOW))
    assert "no run" in schedule_line(make_schedule(next_run_at=None))


def test_schedule_view_lists_the_next_runs() -> None:
    view = ScheduleView(
        schedule=make_schedule(next_run_at=NOW),
        next_runs=(NOW, NOW + timedelta(days=1)),
    )

    rendered = schedule_view(view)

    assert "next:" in rendered
    assert format_instant(NOW) in rendered
    assert format_instant(NOW + timedelta(days=1)) in rendered


def test_schedule_view_without_runs_says_so() -> None:
    rendered = schedule_view(ScheduleView(schedule=make_schedule(), next_runs=()))

    assert "no upcoming occurrences" in rendered


def test_poll_detail_renders_every_field() -> None:
    schedule = make_schedule(poll_id=10)
    detail = PollDetail(
        poll=make_poll(description=None, allows_multiple_answers=True),
        schedules=(ScheduleView(schedule=schedule, next_runs=(NOW,)),),
    )

    rendered = poll_detail(detail)

    assert "Poll #10" in rendered
    assert "options: Reliability, Speed, Docs" in rendered
    assert "description: (none)" in rendered
    assert "multiple answers: yes" in rendered
    assert "schedules:" in rendered
    assert "2026-03-01 09:00 UTC" in rendered


def test_poll_detail_without_schedules_says_so() -> None:
    rendered = poll_detail(PollDetail(poll=make_poll(targets=()), schedules=()))

    assert "targets: (none)" in rendered
    assert "schedules: none" in rendered


def test_poll_content_is_never_escaped() -> None:
    # No parse mode is used, so literal markup must survive untouched (FR-120).
    rendered = poll_detail(PollDetail(poll=make_poll(question="<b>Ship it?</b>"), schedules=()))

    assert "<b>Ship it?</b>" in rendered


def test_long_poll_detail_is_truncated() -> None:
    detail = PollDetail(poll=make_poll(question="q" * 300, options=("o" * 100,) * 2), schedules=())

    assert len(poll_detail(detail)) <= MAX_MESSAGE_LENGTH


def test_page_hint_reports_position_and_total() -> None:
    page = Page.create([1, 2], number=2, total=10)

    assert page_hint(page) == "Page 2/2 - 10 item(s)"


def test_empty_listing_names_the_creating_command() -> None:
    assert "/newpoll" in empty_listing("/newpoll")


def test_status_message_reports_health_without_secrets() -> None:
    rendered = status_message(
        version="0.1.0",
        build_sha="abc1234",
        uptime_seconds=3_600.0,
        db_ok=True,
        heartbeat_age=2.5,
    )

    assert "version: 0.1.0 (abc1234)" in rendered
    assert "uptime: 3600s" in rendered
    assert "database: ok" in rendered
    assert "2.5s ago" in rendered


def test_status_message_handles_a_missing_heartbeat_and_database() -> None:
    rendered = status_message(
        version="dev",
        build_sha="unknown",
        uptime_seconds=0.0,
        db_ok=False,
        heartbeat_age=None,
    )

    assert "database: unavailable" in rendered
    assert "scheduler heartbeat: never" in rendered


# --------------------------------------------------------------------------------------------
# Keyboards
# --------------------------------------------------------------------------------------------


def test_keyboard_limits_match_the_spec() -> None:
    assert MAX_ROWS == 8
    assert MAX_BUTTONS_PER_ROW == 4


def test_a_keyboard_is_built_with_its_labels_and_data() -> None:
    keyboard = build_keyboard([[("Polls", "v1:p:list:1"), ("Chats", "v1:w:cancel:chat")]])

    row = keyboard.inline_keyboard[0]
    assert [(button.text, button.callback_data) for button in row] == [
        ("Polls", "v1:p:list:1"),
        ("Chats", "v1:w:cancel:chat"),
    ]


def test_eight_rows_of_four_buttons_are_allowed() -> None:
    rows = [[("b", f"v1:p:view:{row * 4 + column}") for column in range(4)] for row in range(8)]

    assert len(build_keyboard(rows).inline_keyboard) == 8


def test_more_than_eight_rows_are_rejected() -> None:
    rows = [[("b", "v1:p:view:1")] for _ in range(9)]

    with pytest.raises(KeyboardLayoutError, match="8 rows"):
        build_keyboard(rows)


def test_more_than_four_buttons_in_a_row_are_rejected() -> None:
    row = [("b", "v1:p:view:1") for _ in range(5)]

    with pytest.raises(KeyboardLayoutError, match="4 buttons"):
        build_keyboard([row])


def test_an_empty_row_is_rejected() -> None:
    with pytest.raises(KeyboardLayoutError, match="cannot be empty"):
        build_keyboard([[]])


def test_confirmation_keyboard_encodes_the_entity_id() -> None:
    keyboard = confirmation_keyboard(
        entity="poll", entity_id=12, confirm_action="sendnow", namespace="p"
    )

    confirm, cancel = keyboard.inline_keyboard[0]
    assert decode(str(confirm.callback_data)) == decode("v1:p:sendnow:12")
    assert decode(str(cancel.callback_data)).action == "cancel"


def test_pagination_keyboard_has_both_directions_in_the_middle() -> None:
    keyboard = pagination_keyboard(
        namespace="p", action="list", page=2, has_previous=True, has_next=True
    )

    assert [button.text for button in keyboard.inline_keyboard[0]] == ["Previous", "Next"]
    assert decode(str(keyboard.inline_keyboard[0][0].callback_data)).argument == "1"
    assert decode(str(keyboard.inline_keyboard[0][1].callback_data)).argument == "3"


def test_pagination_keyboard_omits_missing_directions() -> None:
    first = pagination_keyboard(
        namespace="p", action="list", page=1, has_previous=False, has_next=True
    )
    last = pagination_keyboard(
        namespace="p", action="list", page=3, has_previous=True, has_next=False
    )

    assert [button.text for button in first.inline_keyboard[0]] == ["Next"]
    assert [button.text for button in last.inline_keyboard[0]] == ["Previous"]


def test_pagination_keyboard_is_empty_for_a_single_page() -> None:
    keyboard = pagination_keyboard(
        namespace="p", action="list", page=1, has_previous=False, has_next=False
    )

    assert list(keyboard.inline_keyboard) == []


def test_pagination_keyboard_carries_a_filter_argument() -> None:
    keyboard = pagination_keyboard(
        namespace="s",
        action="list",
        page=1,
        has_previous=False,
        has_next=True,
        argument=7,
    )

    assert decode(str(keyboard.inline_keyboard[0][0].callback_data)).argument == "7_2"


def test_keyboards_never_exceed_the_callback_ceiling() -> None:
    keyboard = build_keyboard([[(f"Poll {index}", f"v1:p:view:{index}")] for index in range(8)])

    assert all(
        len(str(button.callback_data).encode()) <= 64
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_a_schedule_kind_is_renderable() -> None:
    assert "monthly" in schedule_line(make_schedule(kind=ScheduleKind.MONTHLY, day_of_month=3))


def test_nothing_depends_on_the_wall_clock() -> None:
    assert format_instant(FIXED_NOW + timedelta(hours=1)).endswith("UTC")
