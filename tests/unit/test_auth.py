"""Authorization and admin budgets (T-038, FR-106 - FR-108): no state change on any refusal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from telegram_auto_poll.bot.auth import (
    ALLOWED,
    GENERIC_REFUSAL,
    AdminFilter,
    AdminGuard,
    AuthDenial,
    command_name,
    rate_limit_message,
)
from tests.conftest import ADMIN_USER_ID
from tests.fakes import FakeClock

START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def build(
    *,
    admins: frozenset[int] = frozenset({ADMIN_USER_ID}),
    clock: FakeClock | None = None,
    commands_per_minute: int = 20,
) -> tuple[AdminGuard, FakeClock]:
    resolved = clock or FakeClock(START)
    guard = AdminGuard(
        admin_user_ids=admins,
        clock=resolved,
        commands_per_minute=commands_per_minute,
    )
    return guard, resolved


def message_update(
    *,
    user_id: int | None = ADMIN_USER_ID,
    chat_type: str = "private",
    text: str | None = "/status",
) -> object:
    return SimpleNamespace(
        message=SimpleNamespace(
            chat=SimpleNamespace(type=chat_type, id=user_id or 1),
            from_user=None if user_id is None else SimpleNamespace(id=user_id),
            text=text,
        )
    )


def callback_update(
    *,
    user_id: int | None = ADMIN_USER_ID,
    data: str | None = "v1:p:view:12",
) -> object:
    return SimpleNamespace(
        callback_query=SimpleNamespace(
            from_user=None if user_id is None else SimpleNamespace(id=user_id),
            data=data,
        )
    )


# --------------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------------


def test_an_allowlisted_private_message_is_accepted() -> None:
    guard, _ = build()

    assert guard.authorize_message(message_update()) == ALLOWED


def test_a_non_allowlisted_private_message_is_refused_with_the_generic_text() -> None:
    guard, _ = build()

    decision = guard.authorize_message(message_update(user_id=999_999_999))

    assert decision.allowed is False
    assert decision.reason is AuthDenial.NOT_ADMIN
    assert GENERIC_REFUSAL == "This is a private bot. Access is restricted to its administrators."


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_group_and_channel_messages_are_ignored(chat_type: str) -> None:
    guard, _ = build()

    decision = guard.authorize_message(message_update(chat_type=chat_type))

    assert decision.allowed is False
    assert decision.reason is AuthDenial.NOT_PRIVATE


def test_a_channel_post_without_a_user_is_not_an_admin() -> None:
    guard, _ = build()

    decision = guard.authorize_message(message_update(user_id=None, chat_type="private"))

    assert decision.allowed is False
    assert decision.reason is AuthDenial.NOT_ADMIN


def test_a_non_message_update_is_not_private() -> None:
    guard, _ = build()

    decision = guard.authorize_message(SimpleNamespace())

    assert decision.allowed is False
    assert decision.reason is AuthDenial.NOT_PRIVATE


# --------------------------------------------------------------------------------------------
# Callbacks
# --------------------------------------------------------------------------------------------


def test_an_allowlisted_callback_is_accepted() -> None:
    guard, _ = build()

    assert guard.authorize_callback(callback_update()) == ALLOWED


def test_a_callback_from_a_non_admin_is_refused() -> None:
    guard, _ = build()

    decision = guard.authorize_callback(callback_update(user_id=555))

    assert decision.allowed is False
    assert decision.reason is AuthDenial.NOT_ADMIN


def test_a_forged_namespace_is_refused() -> None:
    guard, _ = build()

    decision = guard.authorize_callback(callback_update(data="v1:x:delete:1"))

    assert decision.allowed is False
    assert decision.reason is AuthDenial.BAD_CALLBACK


@pytest.mark.parametrize("data", ["v2:p:view:12", "p:view:12", "v1:p", None])
def test_malformed_callback_data_is_refused(data: str | None) -> None:
    guard, _ = build()

    decision = guard.authorize_callback(callback_update(data=data))

    assert decision.allowed is False
    assert decision.reason is AuthDenial.BAD_CALLBACK


def test_a_non_callback_update_is_refused() -> None:
    guard, _ = build()

    decision = guard.authorize_callback(message_update())

    assert decision.allowed is False
    assert decision.reason is AuthDenial.BAD_CALLBACK


# --------------------------------------------------------------------------------------------
# Command budget
# --------------------------------------------------------------------------------------------


def test_twenty_commands_are_allowed_and_the_twenty_first_is_refused() -> None:
    guard, _ = build(commands_per_minute=20)

    for _ in range(20):
        assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED

    decision = guard.consume_command(ADMIN_USER_ID, "status")

    assert decision.allowed is False
    assert decision.reason is AuthDenial.RATE_LIMITED
    assert decision.retry_after == 60


def test_the_budget_recovers_as_the_window_moves() -> None:
    guard, clock = build(commands_per_minute=1)
    assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED

    clock.advance(30)
    refused = guard.consume_command(ADMIN_USER_ID, "status")
    assert refused.retry_after == 30

    clock.advance(30)
    assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED


def test_budgets_are_per_user() -> None:
    guard, _ = build(commands_per_minute=1)
    assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED

    assert guard.consume_command(ADMIN_USER_ID + 1, "status") == ALLOWED


def test_sendnow_has_its_own_five_per_minute_bucket() -> None:
    guard, _ = build(commands_per_minute=20)

    for _ in range(5):
        assert guard.consume_command(ADMIN_USER_ID, "sendnow") == ALLOWED

    refused = guard.consume_command(ADMIN_USER_ID, "sendnow")
    assert refused.allowed is False
    assert refused.reason is AuthDenial.RATE_LIMITED
    assert refused.retry_after == 60

    # The general budget is untouched by the exhausted sendnow bucket.
    assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED


def test_a_refused_sendnow_does_not_charge_the_general_budget() -> None:
    guard, _ = build(commands_per_minute=6)

    for _ in range(5):
        assert guard.consume_command(ADMIN_USER_ID, "sendnow") == ALLOWED
    refused = guard.consume_command(ADMIN_USER_ID, "sendnow")
    assert refused.reason is AuthDenial.RATE_LIMITED

    # The five allowed sendnow commands used five of six general slots; the refused sixth must not
    # have consumed the last one.
    assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED
    assert guard.consume_command(ADMIN_USER_ID, "status").allowed is False


def test_a_refused_command_does_not_consume_budget_twice() -> None:
    guard, clock = build(commands_per_minute=1)
    guard.consume_command(ADMIN_USER_ID, "status")

    refused = guard.consume_command(ADMIN_USER_ID, "status")
    assert refused.allowed is False

    clock.advance(60)
    assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED


def test_old_timestamps_are_pruned() -> None:
    guard, clock = build(commands_per_minute=2)
    for _ in range(2):
        guard.consume_command(ADMIN_USER_ID, "status")

    clock.advance(61)
    assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED
    assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED


def test_rate_limit_message_states_the_wait() -> None:
    assert rate_limit_message(30) == "Too many commands. Try again in 30 s."


# --------------------------------------------------------------------------------------------
# Command parsing and the PTB filter
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/status", "status"),
        ("/status@MyBot", "status"),
        ("  /status", None),
        ("hello", None),
        ("/", None),
        (None, None),
    ],
)
def test_command_name_extraction(text: str | None, expected: str | None) -> None:
    assert command_name(text) == expected


def test_the_ptb_filter_only_passes_allowlisted_private_messages() -> None:
    guard, _ = build()
    subject = AdminFilter(guard)

    assert subject.filter(message_update()) is True  # type: ignore[arg-type]
    assert subject.filter(message_update(user_id=42)) is False  # type: ignore[arg-type]
    assert subject.filter(message_update(chat_type="group")) is False  # type: ignore[arg-type]


def test_the_filter_does_not_consume_budget() -> None:
    guard, _ = build(commands_per_minute=1)
    subject = AdminFilter(guard)

    for _ in range(5):
        subject.filter(message_update())  # type: ignore[arg-type]

    assert guard.consume_command(ADMIN_USER_ID, "status") == ALLOWED


def test_a_clock_jump_backwards_does_not_grant_extra_budget() -> None:
    guard, clock = build(commands_per_minute=1)
    guard.consume_command(ADMIN_USER_ID, "status")

    clock.set(START - timedelta(minutes=5))

    # Timestamps "in the future" are not older than the window, so the budget stays spent.
    assert guard.consume_command(ADMIN_USER_ID, "status").allowed is False
