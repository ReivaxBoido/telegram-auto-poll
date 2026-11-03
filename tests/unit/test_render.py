"""Plain-text admin rendering (SPEC-003 §8.6)."""

from __future__ import annotations

from telegram_auto_poll.bot.render import (
    chat_line,
    empty_listing,
    instance_line,
    page_hint,
    poll_detail,
    poll_line,
    publish_confirmation,
    response_page,
    status_message,
    target_selection,
    truncate,
)
from telegram_auto_poll.domain.enums import InstanceStatus, PollStatus
from telegram_auto_poll.services.pagination import Page
from telegram_auto_poll.services.poll_service import PollDetail, PollSummary, ResponsePage
from telegram_auto_poll.services.status_service import StatusSnapshot
from tests.factories import make_chat, make_instance, make_message, make_option, make_poll


def test_truncate_and_small_helpers() -> None:
    assert truncate("abc", limit=5) == "abc"
    assert truncate("abcdef", limit=5) == "ab..."
    assert chat_line(make_chat()).startswith("#1 Automation test group")
    assert empty_listing("/newpoll").endswith("Use /newpoll to add one.")
    assert page_hint(Page.create([1, 2], number=1, total=2)).startswith("Page 1/1")
    assert instance_line(None) == "never published"
    assert "open" in instance_line(make_instance())
    assert "closed" in instance_line(make_instance(status=InstanceStatus.CLOSED))
    assert "deleted" in instance_line(make_instance(status=InstanceStatus.DELETED))


def test_poll_line_and_detail() -> None:
    poll = make_poll(status=PollStatus.ACTIVE)
    summary = PollSummary(poll=poll, latest_instance=make_instance(), response_count=3)
    assert "3 responses" in poll_line(summary)
    assert "never published" in poll_line(
        PollSummary(poll=make_poll(), latest_instance=None, response_count=0)
    )

    detail = PollDetail(
        poll=poll,
        latest_instance=make_instance(),
        counts={poll.options[0].id or 0: 2},
        total_respondents=2,
    )
    rendered = poll_detail(detail)
    assert "1. In - 2" in rendered
    assert "latest instance: #100 open" in rendered

    draft = PollDetail(poll=make_poll(), latest_instance=None, counts={}, total_respondents=0)
    assert "none (use Publish)" in poll_detail(draft)


def test_target_selection_and_confirmation() -> None:
    poll = make_poll()
    text = target_selection(poll, selected=frozenset({1}), page=1, pages=2)
    assert "selected: 1 chat(s)" in text
    assert "Page 1/2" in text

    confirmation = publish_confirmation(poll, [make_chat()])
    assert "to 1 chat(s)" in confirmation
    assert "shared" in confirmation


def test_response_page() -> None:
    poll = make_poll()
    page = ResponsePage(
        poll=poll,
        instance=make_instance(),
        option=make_option(0, label="In"),
        names=("Alice", "Bob"),
        number=1,
        pages=1,
        total=2,
    )
    rendered = response_page(page)
    assert "Option 1: In" in rendered
    assert "Alice" in rendered


def test_status_message_includes_counts_and_failures() -> None:
    snapshot = StatusSnapshot(
        active_chats=1,
        polls=2,
        open_instances=3,
        pending_messages=4,
        active_responses=5,
        recent_failures=(make_message(id=9, last_error="bad_request"),),
    )
    rendered = status_message(
        version="0.3.0",
        build_sha="abc",
        uptime_seconds=12,
        db_ok=True,
        heartbeat_age=1.5,
        snapshot=snapshot,
    )
    assert "version: 0.3.0 (abc)" in rendered
    assert "posting worker heartbeat: 1.5s ago" in rendered
    assert "message #9" in rendered
