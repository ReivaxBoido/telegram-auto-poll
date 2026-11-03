"""Plain-text admin messages (SPEC-003 FR-326, §8.6).

No parse mode is ever used, so user-supplied text is shown literally. Every renderer truncates to
Telegram's 4,096-character message limit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..domain.entities import Chat, Poll, PollInstance
from ..domain.enums import InstanceStatus, PollStatus
from ..services.pagination import Page
from ..services.poll_service import PollDetail, PollSummary, ResponsePage
from ..services.status_service import StatusSnapshot

MAX_MESSAGE_LENGTH = 4_096
ELLIPSIS = "..."


def truncate(text: str, *, limit: int = MAX_MESSAGE_LENGTH) -> str:
    """Return ``text`` unchanged, or shortened to fit one Telegram message."""
    if len(text) <= limit:
        return text
    return text[: limit - len(ELLIPSIS)] + ELLIPSIS


def chat_line(chat: Chat) -> str:
    """One row of ``/chats``."""
    state = "active" if chat.is_active else "inactive"
    return f"#{chat.id} {chat.title} ({chat.type}) - {state}"


def poll_line(summary: PollSummary) -> str:
    """One row of ``/polls``."""
    poll = summary.poll
    state = "deleted" if poll.status is PollStatus.DELETED else poll.status.value
    instance = (
        "never published"
        if summary.latest_instance is None
        else summary.latest_instance.status.value
    )
    return (
        f"#{poll.id} {poll.title} - {state}, {len(poll.options)} options, "
        f"{summary.response_count} responses, latest: {instance}"
    )


def poll_detail(detail: PollDetail) -> str:
    """Poll definition, option counts, and latest instance state."""
    poll = detail.poll
    lines = [
        f"Poll #{poll.id}",
        f"title: {poll.title}",
        f"status: {poll.status.value}",
        "options:",
    ]
    for index, option in enumerate(poll.options, start=1):
        count = detail.counts.get(option.id or 0, 0)
        lines.append(f"  {index}. {option.label} - {count}")
    lines.append(f"respondents: {detail.total_respondents}")
    if detail.latest_instance is None:
        lines.append("latest instance: none (use Publish)")
    else:
        lines.append(
            f"latest instance: #{detail.latest_instance.id} {detail.latest_instance.status.value}"
        )
    return truncate("\n".join(lines))


def target_selection(
    poll: Poll,
    *,
    selected: frozenset[int],
    page: int,
    pages: int,
) -> str:
    """Prompt for the publish target selection."""
    return (
        f'Publish poll #{poll.id} "{poll.title}"\n'
        f"selected: {len(selected)} chat(s)\n"
        "Tap a chat to toggle it, then Done.\n\n"
        "Responses are shared across every selected chat for this instance.\n"
        f"Page {page}/{pages}"
    )


def publish_confirmation(poll: Poll, chats: Sequence[Chat]) -> str:
    """Confirmation summary before creating an instance."""
    names = ", ".join(chat.title for chat in chats)
    return truncate(
        "\n".join(
            [
                f'Publish poll #{poll.id} "{poll.title}" to {len(chats)} chat(s)?',
                f"targets: {names}",
                "Responses will be shared across all selected chats.",
            ]
        )
    )


def response_page(page: ResponsePage) -> str:
    """One page of names for one option."""
    header = (
        f'Poll #{page.poll.id} "{page.poll.title}"\n'
        f"Option {page.option.position + 1}: {page.option.label}\n"
        f"{page.total} response(s), page {page.number}/{page.pages}"
    )
    body = "\n".join(page.names) if page.names else "(nobody yet)"
    return truncate(f"{header}\n\n{body}")


def page_hint(page: Page[Any]) -> str:
    """Footer for a paginated listing."""
    return f"Page {page.number}/{page.pages} - {page.total} item(s)"


def empty_listing(creating_command: str) -> str:
    """Explain how to create the first item."""
    return f"Nothing here yet. Use {creating_command} to add one."


def status_message(
    *,
    version: str,
    build_sha: str,
    uptime_seconds: float,
    db_ok: bool,
    heartbeat_age: float | None,
    snapshot: StatusSnapshot,
) -> str:
    """The health and count block of ``/status`` (FR-338), without names."""
    heartbeat = "never" if heartbeat_age is None else f"{heartbeat_age:.1f}s ago"
    lines = [
        f"version: {version} ({build_sha})",
        f"uptime: {uptime_seconds:.0f}s",
        f"database: {'ok' if db_ok else 'unavailable'}",
        f"posting worker heartbeat: {heartbeat}",
        f"chats: {snapshot.active_chats} active",
        f"polls: {snapshot.polls}",
        f"open instances: {snapshot.open_instances}",
        f"pending messages: {snapshot.pending_messages}",
        f"active responses: {snapshot.active_responses}",
    ]
    if snapshot.recent_failures:
        lines.append("recent failures:")
        lines.extend(
            f"  message #{message.id} instance #{message.instance_id}: "
            f"{message.last_error or 'unknown'}"
            for message in snapshot.recent_failures
        )
    return "\n".join(lines)


def instance_line(instance: PollInstance | None) -> str:
    """Describe the latest instance state."""
    if instance is None:
        return "never published"
    if instance.status is InstanceStatus.OPEN:
        return f"#{instance.id} open"
    if instance.status is InstanceStatus.CLOSED:
        return f"#{instance.id} closed"
    return f"#{instance.id} deleted"
