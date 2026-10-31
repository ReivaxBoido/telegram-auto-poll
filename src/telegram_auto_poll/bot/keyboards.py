"""Inline keyboards with the layout limits from spec §8.4 (at most 8 rows, 4 buttons per row)."""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..domain.entities import Chat
from ..services.pagination import Page
from .callbacks import (
    WIZARD_ADD_OPTION,
    WIZARD_ANONYMITY,
    WIZARD_BACK,
    WIZARD_CANCEL,
    WIZARD_CONFIRM,
    WIZARD_DONE,
    WIZARD_KIND,
    WIZARD_MULTIPLE,
    WIZARD_NEXT,
    WIZARD_OPTION_REMOVE,
    WIZARD_SKIP,
    WIZARD_TARGET,
    WIZARD_TARGET_PAGE,
    WIZARD_WEEKDAY,
    encode,
    wizard_action,
)
from .wizards.states import WizardState, WizardStep

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
                (confirm_label, encode(namespace, confirm_action, f"{entity}_{entity_id}")),
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


WIZARD_CHAT_PAGE_SIZE = 6
MAX_BUTTON_TEXT = 64
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def chat_button_label(chat: Chat, *, selected: bool) -> str:
    """Label a target chat with a selected marker, its title, and its Telegram id."""
    marker = "\u2705 " if selected else ""
    suffix = f" ({chat.telegram_chat_id})"
    budget = MAX_BUTTON_TEXT - len(marker) - len(suffix) - 1
    title = chat.title
    if len(title) > budget:
        title = title[: max(budget - 1, 1)].rstrip() + "\u2026"
    return f"{marker}{title}{suffix}"


def _navigation() -> list[tuple[str, str]]:
    return [
        ("\u2b05 Back", wizard_action(WIZARD_BACK)),
        ("\u2716 Cancel", wizard_action(WIZARD_CANCEL)),
    ]


def wizard_keyboard(
    state: WizardState, *, chat_page: Page[Chat] | None = None
) -> InlineKeyboardMarkup:
    """Build the inline keyboard for the current wizard step."""
    step = state.step
    rows: list[list[Button]] = []

    if step in {
        WizardStep.QUESTION,
        WizardStep.SCHEDULE_DAY,
        WizardStep.SCHEDULE_TIME,
        WizardStep.SCHEDULE_RUN_AT,
    }:
        rows.append(_navigation())
    elif step is WizardStep.DESCRIPTION:
        rows.append([("Skip", wizard_action(WIZARD_SKIP)), *_navigation()])
    elif step is WizardStep.OPTIONS:
        remove = [
            (f"Remove {index + 1}", wizard_action(WIZARD_OPTION_REMOVE, index))
            for index in range(len(state.options))
        ]
        rows.extend([remove[index : index + 4] for index in range(0, len(remove), 4)])
        if len(state.options) < 12:
            rows.append([("\u2795 Add option", wizard_action(WIZARD_ADD_OPTION))])
        rows.append([("\u2714 Done", wizard_action(WIZARD_DONE)), *_navigation()])
    elif step is WizardStep.ANONYMITY:
        label = "Anonymous: \u2705" if state.is_anonymous else "Anonymous: \u274c"
        rows.append(
            [(label, wizard_action(WIZARD_ANONYMITY)), ("Next", wizard_action(WIZARD_NEXT))]
        )
        rows.append(_navigation())
    elif step is WizardStep.MULTIPLE:
        label = "Multiple: \u2705" if state.allows_multiple_answers else "Single: \u274c"
        rows.append([(label, wizard_action(WIZARD_MULTIPLE)), ("Next", wizard_action(WIZARD_NEXT))])
        rows.append(_navigation())
    elif step is WizardStep.TARGETS and chat_page is not None:
        for chat in chat_page.items:
            rows.append(
                [
                    (
                        chat_button_label(chat, selected=chat.id in state.targets),
                        wizard_action(WIZARD_TARGET, chat.id),
                    )
                ]
            )
        nav: list[Button] = []
        if chat_page.has_previous:
            nav.append(("\u25c0 Previous", wizard_action(WIZARD_TARGET_PAGE, chat_page.number - 1)))
        if chat_page.has_next:
            nav.append(("Next \u25b6", wizard_action(WIZARD_TARGET_PAGE, chat_page.number + 1)))
        if nav:
            rows.append(nav)
        rows.append([("\u2714 Done", wizard_action(WIZARD_DONE)), *_navigation()])
    elif step is WizardStep.SCHEDULE_KIND:
        rows.extend(
            [
                [
                    ("Daily", wizard_action(WIZARD_KIND, "daily")),
                    ("Weekly", wizard_action(WIZARD_KIND, "weekly")),
                ],
                [
                    ("Monthly", wizard_action(WIZARD_KIND, "monthly")),
                    ("One-off", wizard_action(WIZARD_KIND, "once")),
                ],
                [("Save without schedule", wizard_action(WIZARD_KIND, "none"))],
                _navigation(),
            ]
        )
    elif step is WizardStep.SCHEDULE_WEEKDAYS:
        days = [
            (
                f"{'\u2705 ' if day in state.weekdays else ''}{WEEKDAY_NAMES[day - 1]}",
                wizard_action(WIZARD_WEEKDAY, day),
            )
            for day in range(1, 8)
        ]
        rows.extend([days[:4], days[4:]])
        rows.append([("\u2714 Done", wizard_action(WIZARD_DONE)), *_navigation()])
    elif step in {WizardStep.SCHEDULE_TIMEZONE, WizardStep.SCHEDULE_START, WizardStep.SCHEDULE_END}:
        rows.append([("Skip", wizard_action(WIZARD_SKIP)), *_navigation()])
    elif step is WizardStep.PREVIEW:
        rows.append(
            [
                ("\u2705 Create poll", wizard_action(WIZARD_CONFIRM)),
                ("\u2b05 Back", wizard_action(WIZARD_BACK)),
                ("\u2716 Cancel", wizard_action(WIZARD_CANCEL)),
            ]
        )

    return build_keyboard(rows)
