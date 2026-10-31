"""Inline keyboards with the layout limits from spec §8.4 (at most 8 rows, 4 buttons per row)."""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .callbacks import encode

MAX_ROWS = 8
MAX_BUTTONS_PER_ROW = 4


class KeyboardLayoutError(ValueError):
    """A keyboard exceeds Telegram's or the spec's layout limits."""


Button = tuple[str, str]


def build_keyboard(rows: Sequence[Sequence[Button]]) -> InlineKeyboardMarkup:
    """Build a keyboard, validating the row and button counts first.

    Raises:
        KeyboardLayoutError: more than 8 rows, an empty row, or more than 4 buttons in a row.
    """
    if len(rows) > MAX_ROWS:
        raise KeyboardLayoutError(f"at most {MAX_ROWS} rows are allowed (got {len(rows)})")
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


def confirmation_keyboard(
    *,
    entity: str,
    entity_id: int,
    confirm_action: str,
    namespace: str = "p",
    confirm_label: str = "Confirm",
    cancel_label: str = "Cancel",
) -> InlineKeyboardMarkup:
    """Two buttons: confirm the ID or cancel the confirmation."""
    return build_keyboard(
        [
            [
                (confirm_label, encode(namespace, confirm_action, entity_id)),
                (cancel_label, encode("w", "cancel", entity)),
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


def _paged(namespace: str, action: str, page: int, argument: str | int | None) -> str:
    if argument is None:
        return encode(namespace, action, page)
    return encode(namespace, action, f"{argument}_{page}")
