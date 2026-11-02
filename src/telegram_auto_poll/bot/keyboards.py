"""Inline keyboards with the layout limits from spec §8.6 (at most 8 rows, 4 buttons per row)."""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..domain.callbacks import (
    confirm,
    encode,
    poll_action,
    target_done,
    target_page,
    target_toggle,
    wizard_action,
)
from ..domain.entities import Chat, Poll, PollInstance, PollRender
from ..domain.enums import InstanceStatus, PollStatus
from ..domain.poll_render import keyboard_rows

MAX_ROWS = 8
VOTE_MAX_ROWS = 10
MAX_BUTTONS_PER_ROW = 4
TARGET_PAGE_SIZE = 6
CHAT_LABEL_MAX = 40

Button = tuple[str, str]


class KeyboardLayoutError(ValueError):
    """A keyboard exceeds Telegram's or the spec's layout limits."""


def build_keyboard(
    rows: Sequence[Sequence[Button]], *, max_rows: int = MAX_ROWS
) -> InlineKeyboardMarkup:
    """Build a keyboard, validating the row and button counts first.

    Raises:
        KeyboardLayoutError: more than ``max_rows`` rows, an empty row, or more than 4 buttons in a
            row. The vote keyboard uses one row per option and allows up to ``VOTE_MAX_ROWS``.
    """
    if len(rows) > max_rows:
        raise KeyboardLayoutError(f"at most {max_rows} rows are allowed (got {len(rows)})")
    built: list[list[InlineKeyboardButton]] = []
    for row in rows:
        if not row:
            raise KeyboardLayoutError("a keyboard row cannot be empty")
        if len(row) > MAX_BUTTONS_PER_ROW:
            raise KeyboardLayoutError(
                f"at most {MAX_BUTTONS_PER_ROW} buttons per row (got {len(row)})"
            )
        built.append([InlineKeyboardButton(text=label, callback_data=data) for label, data in row])
    return InlineKeyboardMarkup(built)


def vote_keyboard(render: PollRender) -> InlineKeyboardMarkup | None:
    """Build the participant keyboard from a rendered poll, or ``None`` when closed."""
    rows = keyboard_rows(render)
    if not rows:
        return None
    return build_keyboard(rows, max_rows=VOTE_MAX_ROWS)


def confirmation_keyboard(
    *,
    entity: str,
    entity_id: int,
    confirm_action: str = "confirm",
    confirm_label: str = "Confirm",
    cancel_label: str = "Cancel",
) -> InlineKeyboardMarkup:
    """Two buttons: confirm the ID or cancel the confirmation."""
    return build_keyboard(
        [
            [
                (confirm_label, confirm(confirm_action, entity_id)),
                (cancel_label, wizard_action("cancel", entity)),
            ]
        ]
    )


def pagination_keyboard(
    *,
    namespace: str,
    action: str,
    page: int,
    has_previous: bool,
    has_next: bool,
    argument: str | int | None = None,
) -> InlineKeyboardMarkup:
    """Previous/Next buttons, each omitted at the ends of the listing."""
    row: list[Button] = []
    if has_previous:
        row.append(("Previous", _paged(namespace, action, page - 1, argument)))
    if has_next:
        row.append(("Next", _paged(namespace, action, page + 1, argument)))
    if not row:
        return build_keyboard([])
    return build_keyboard([row])


def poll_detail_keyboard(poll: Poll, latest: PollInstance | None) -> InlineKeyboardMarkup:
    """Actions available from ``/poll <id>``."""
    poll_id = poll.id or 0
    rows: list[list[Button]] = []
    if poll.status is not PollStatus.DELETED:
        rows.append([("Publish", poll_action("pub", poll_id))])
    if latest is not None and latest.status is InstanceStatus.OPEN:
        rows.append([("Close", poll_action("close", poll_id))])
    elif latest is not None and latest.status is InstanceStatus.CLOSED:
        rows.append([("Reopen", poll_action("reopen", poll_id))])
    if latest is not None:
        rows.append(
            [
                ("Refresh", poll_action("ref", poll_id)),
                ("Responses", poll_action("resp", poll_id)),
            ]
        )
    if poll.status is not PollStatus.DELETED:
        rows.append([("Delete", poll_action("del", poll_id))])
    return build_keyboard(rows)


def target_keyboard(
    *,
    poll_id: int,
    chats: Sequence[Chat],
    selected: frozenset[int],
    page: int,
    has_previous: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    """Target selection: six chats per page, one row each, plus navigation and actions."""
    rows: list[list[Button]] = []
    for chat in chats:
        marker = "[x]" if (chat.id or 0) in selected else "[ ]"
        label = _chat_label(chat)
        rows.append([(f"{marker} {label}", target_toggle(poll_id, chat.id or 0))])
    navigation: list[Button] = []
    if has_previous:
        navigation.append(("Previous", target_page(poll_id, page - 1)))
    if has_next:
        navigation.append(("Next", target_page(poll_id, page + 1)))
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            ("Done", target_done(poll_id)),
            ("Cancel", encode("t", "cancel", poll_id)),
        ]
    )
    return build_keyboard(rows)


def _chat_label(chat: Chat) -> str:
    label = f"{chat.title} ({chat.type})"
    if len(label) <= CHAT_LABEL_MAX:
        return label
    return label[: CHAT_LABEL_MAX - 1] + "…"


def _paged(namespace: str, action: str, page: int, argument: str | int | None) -> str:
    if argument is None:
        return encode(namespace, action, page)
    return encode(namespace, action, f"{argument}_{page}")
