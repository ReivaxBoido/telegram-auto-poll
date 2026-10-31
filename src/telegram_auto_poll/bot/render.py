"""Plain-text admin messages (FR-120, spec §8.4).

No parse mode is ever used, so user-supplied text is shown literally: escaping would corrupt a
question that legitimately contains ``<b>``. Every renderer truncates to Telegram's 4,096-character
message limit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain.entities import Chat, Poll, Schedule
from ..services.pagination import Page
from ..services.poll_service import PollDetail, ScheduleView

MAX_MESSAGE_LENGTH = 4_096
ELLIPSIS = "..."


def truncate(text: str, *, limit: int = MAX_MESSAGE_LENGTH) -> str:
    """Return ``text`` unchanged, or shortened to fit one Telegram message."""
    if len(text) <= limit:
        return text
    return text[: limit - len(ELLIPSIS)] + ELLIPSIS


def format_instant(moment: datetime) -> str:
    """Render an instant as ``YYYY-MM-DD HH:MM UTC``."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def chat_line(chat: Chat) -> str:
    """One row of ``/chats``."""
    state = "active" if chat.is_active else "inactive"
    return f"#{chat.id} {chat.title} ({chat.type}) - {state}"


def poll_line(poll: Poll) -> str:
    """One row of ``/polls``."""
    targets = len(poll.targets)
    state = "archived" if poll.is_archived else "active"
    return f"#{poll.id} {poll.question} - {len(poll.options)} options, {targets} target(s), {state}"


def schedule_line(schedule: Schedule) -> str:
    """One row of ``/schedules``."""
    if schedule.is_paused:
        when = "paused"
    elif schedule.next_run_at is not None:
        when = format_instant(schedule.next_run_at)
    else:
        when = "no run"
    return f"#{schedule.id} {schedule.kind} {schedule.timezone} - {when}"


def schedule_view(view: ScheduleView) -> str:
    """Schedule detail with the next five occurrences (FR-123)."""
    lines = [schedule_line(view.schedule)]
    if view.next_runs:
        lines.append("next:")
        lines.extend(f"  {format_instant(run)}" for run in view.next_runs)
    else:
        lines.append("no upcoming occurrences")
    return truncate("\n".join(lines))


def poll_detail(detail: PollDetail) -> str:
    """Poll definition, targets, and schedule summaries (FR-119)."""
    poll = detail.poll
    lines = [
        f"Poll #{poll.id}",
        f"question: {poll.question}",
        f"options: {', '.join(poll.options)}",
        f"description: {poll.description or '(none)'}",
        f"anonymous: {'yes' if poll.is_anonymous else 'no'}",
        f"multiple answers: {'yes' if poll.allows_multiple_answers else 'no'}",
        f"targets: {', '.join(f'#{chat_id}' for chat_id in poll.targets) or '(none)'}",
        f"archived: {'yes' if poll.is_archived else 'no'}",
    ]
    if detail.schedules:
        lines.append("schedules:")
        lines.extend(f"  {schedule_view(view)}" for view in detail.schedules)
    else:
        lines.append("schedules: none")
    return truncate("\n".join(lines))


def page_hint[T](page: Page[T]) -> str:
    """Footer for a paginated listing."""
    return f"Page {page.number}/{page.pages} - {page.total} item(s)"


def empty_listing(creating_command: str) -> str:
    """Explain how to create the first item (spec §8.5)."""
    return f"Nothing here yet. Use {creating_command} to add one."


def status_message(
    *,
    version: str,
    build_sha: str,
    uptime_seconds: float,
    db_ok: bool,
    heartbeat_age: float | None,
) -> str:
    """The health block of ``/status`` (FR-109), without secrets."""
    heartbeat = "never" if heartbeat_age is None else f"{heartbeat_age:.1f}s ago"
    return "\n".join(
        [
            f"version: {version} ({build_sha})",
            f"uptime: {uptime_seconds:.0f}s",
            f"database: {'ok' if db_ok else 'unavailable'}",
            f"scheduler heartbeat: {heartbeat}",
        ]
    )
