"""Buttons-first wizard keyboards (T-203, FR-201, FR-204 - FR-210, FR-214)."""

from __future__ import annotations

import pytest

from telegram_auto_poll.bot.callbacks import WIZARD_BACK, WIZARD_CANCEL, decode
from telegram_auto_poll.bot.keyboards import (
    MAX_BUTTONS_PER_ROW,
    MAX_ROWS,
    WIZARD_CHAT_PAGE_SIZE,
    chat_button_label,
    wizard_keyboard,
)
from telegram_auto_poll.bot.wizards.states import WizardState, WizardStep
from telegram_auto_poll.services.pagination import Page
from tests.factories import make_chat

CHAT_BASE = -1_000_000_000_000


def state(step: WizardStep, **fields: object) -> WizardState:
    return WizardState(admin_user_id=1, step=step, **fields)  # type: ignore[arg-type]


def actions(keyboard: object) -> list[str]:
    return [
        decode(str(button.callback_data)).action
        for row in keyboard.inline_keyboard
        for button in row
    ]  # type: ignore[attr-defined]


def chats(count: int, *, title: str = "Ops") -> list[object]:
    return [
        make_chat(id=index + 1, telegram_chat_id=CHAT_BASE + index, title=title)
        for index in range(count)
    ]


def page(items: list[object], *, number: int, total: int) -> Page[object]:
    return Page.create(items, number=number, size=WIZARD_CHAT_PAGE_SIZE, total=total)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "step",
    [
        WizardStep.QUESTION,
        WizardStep.DESCRIPTION,
        WizardStep.OPTIONS,
        WizardStep.ANONYMITY,
        WizardStep.MULTIPLE,
        WizardStep.SCHEDULE_KIND,
        WizardStep.SCHEDULE_WEEKDAYS,
        WizardStep.SCHEDULE_DAY,
        WizardStep.SCHEDULE_TIME,
        WizardStep.SCHEDULE_RUN_AT,
        WizardStep.SCHEDULE_TIMEZONE,
        WizardStep.SCHEDULE_START,
        WizardStep.SCHEDULE_END,
        WizardStep.PREVIEW,
    ],
)
def test_every_step_has_back_and_cancel_and_respects_the_limits(step: WizardStep) -> None:
    keyboard = wizard_keyboard(state(step, options=("A", "B")))

    found = actions(keyboard)
    assert WIZARD_BACK in found
    assert WIZARD_CANCEL in found
    assert len(keyboard.inline_keyboard) <= MAX_ROWS
    assert all(len(row) <= MAX_BUTTONS_PER_ROW for row in keyboard.inline_keyboard)


def test_targets_without_a_page_render_an_empty_keyboard() -> None:
    assert list(wizard_keyboard(state(WizardStep.TARGETS)).inline_keyboard) == []


def test_target_buttons_show_title_id_and_selection_and_paginate() -> None:
    items = chats(6)
    keyboard = wizard_keyboard(
        state(WizardStep.TARGETS, targets=frozenset({1})),
        chat_page=page(items, number=2, total=13),
    )

    first = keyboard.inline_keyboard[0][0]
    assert first.text.startswith("\u2705 Ops")
    assert str(CHAT_BASE) in first.text
    assert len(keyboard.inline_keyboard) <= MAX_ROWS
    nav = keyboard.inline_keyboard[6]
    assert [button.text for button in nav] == ["\u25c0 Previous", "Next \u25b6"]
    assert decode(str(nav[0].callback_data)).argument == "1"
    assert decode(str(nav[1].callback_data)).argument == "3"


def test_the_first_target_page_has_no_previous_button() -> None:
    keyboard = wizard_keyboard(
        state(WizardStep.TARGETS), chat_page=page(chats(6), number=1, total=13)
    )

    nav = keyboard.inline_keyboard[6]
    assert [button.text for button in nav] == ["Next \u25b6"]


def test_the_last_target_page_has_no_next_button() -> None:
    keyboard = wizard_keyboard(
        state(WizardStep.TARGETS), chat_page=page(chats(1), number=3, total=13)
    )

    assert [button.text for button in keyboard.inline_keyboard[1]] == ["\u25c0 Previous"]


def test_a_long_title_is_truncated_but_the_id_survives() -> None:
    label = chat_button_label(
        make_chat(title="A" * 200, telegram_chat_id=CHAT_BASE), selected=False
    )

    assert len(label) <= 64
    assert label.endswith(f" ({CHAT_BASE})")
    assert "\u2026" in label


def test_options_render_only_remove_buttons() -> None:
    keyboard = wizard_keyboard(state(WizardStep.OPTIONS, options=tuple("abcdefghijkl")))

    found = actions(keyboard)
    assert WIZARD_BACK in found
    assert "add" not in found
    assert found.count("opt") == 12


def test_weekdays_show_their_selection() -> None:
    keyboard = wizard_keyboard(state(WizardStep.SCHEDULE_WEEKDAYS, weekdays=(1, 7)))

    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "\u2705 Mon" in labels
    assert "\u2705 Sun" in labels
    assert "Tue" in labels


def test_anonymity_and_multiple_show_their_state() -> None:
    anonymous = wizard_keyboard(state(WizardStep.ANONYMITY, is_anonymous=True))
    named = wizard_keyboard(state(WizardStep.ANONYMITY, is_anonymous=False))
    multiple = wizard_keyboard(state(WizardStep.MULTIPLE, allows_multiple_answers=True))

    assert anonymous.inline_keyboard[0][0].text.endswith("\u2705")
    assert named.inline_keyboard[0][0].text.endswith("\u274c")
    assert multiple.inline_keyboard[0][0].text.startswith("Multiple")


def test_a_single_target_page_has_no_navigation_row() -> None:
    keyboard = wizard_keyboard(
        state(WizardStep.TARGETS), chat_page=page(chats(2), number=1, total=2)
    )

    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "Previous" not in " ".join(labels)
    assert "Next" not in " ".join(labels)
