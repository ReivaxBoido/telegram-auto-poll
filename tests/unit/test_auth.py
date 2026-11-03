"""Admin and participant authorization (SPEC-003 FR-318, FR-319, ADR-0004)."""

from __future__ import annotations

from types import SimpleNamespace

from telegram_auto_poll.bot.auth import (
    AdminFilter,
    AdminGuard,
    AuthDenial,
    command_name,
    rate_limit_message,
    vote_rate_limit_message,
)
from tests.fakes import FakeClock

ADMIN = 100_000_001
OTHER = 42


def _message_update(*, chat_type: str = "private", user_id: int = ADMIN) -> object:
    return SimpleNamespace(
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=chat_type),
            from_user=SimpleNamespace(id=user_id),
        )
    )


def _callback_update(
    data: str,
    *,
    chat_type: str = "private",
    user_id: int = ADMIN,
    message: bool = True,
) -> object:
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        data=data,
        message=(
            SimpleNamespace(
                chat=SimpleNamespace(id=user_id, type=chat_type),
                message_id=10,
            )
            if message
            else None
        ),
    )
    return SimpleNamespace(callback_query=query)


def test_authorize_message_accepts_only_private_admins() -> None:
    guard = AdminGuard(admin_user_ids=frozenset({ADMIN}), clock=FakeClock())

    assert guard.authorize_message(_message_update()).allowed is True
    denied_group = guard.authorize_message(_message_update(chat_type="supergroup"))
    assert denied_group.allowed is False
    assert denied_group.reason is AuthDenial.NOT_PRIVATE
    denied_user = guard.authorize_message(_message_update(user_id=OTHER))
    assert denied_user.reason is AuthDenial.NOT_ADMIN
    assert guard.authorize_message(object()).reason is AuthDenial.NOT_PRIVATE


def test_authorize_admin_callback_requires_private_admin_and_admin_namespace() -> None:
    guard = AdminGuard(admin_user_ids=frozenset({ADMIN}), clock=FakeClock())

    assert guard.authorize_admin_callback(_callback_update("v1:p:close:1")).allowed is True
    assert (
        guard.authorize_admin_callback(_callback_update("v1:p:close:1", chat_type="group")).reason
        is AuthDenial.NOT_PRIVATE
    )
    assert (
        guard.authorize_admin_callback(_callback_update("v1:p:close:1", user_id=OTHER)).reason
        is AuthDenial.NOT_ADMIN
    )
    assert (
        guard.authorize_admin_callback(_callback_update("v1:v:toggle:1_1")).reason
        is AuthDenial.BAD_CALLBACK
    )
    assert guard.authorize_admin_callback(_callback_update("bad")).reason is AuthDenial.BAD_CALLBACK
    assert guard.authorize_admin_callback(object()).reason is AuthDenial.BAD_CALLBACK


def test_authorize_vote_callback_accepts_any_actor_with_vote_namespace() -> None:
    guard = AdminGuard(admin_user_ids=frozenset({ADMIN}), clock=FakeClock())

    assert guard.authorize_vote_callback(_callback_update("v1:v:toggle:1_1", user_id=OTHER)).allowed
    assert (
        guard.authorize_vote_callback(_callback_update("v1:p:close:1")).reason
        is AuthDenial.BAD_CALLBACK
    )
    assert guard.authorize_vote_callback(object()).reason is AuthDenial.BAD_CALLBACK


def test_command_budget_and_vote_budget() -> None:
    clock = FakeClock()
    guard = AdminGuard(
        admin_user_ids=frozenset({ADMIN}),
        clock=clock,
        commands_per_minute=2,
        sendnow_per_minute=1,
        votes_per_minute=2,
    )

    assert guard.consume_command(ADMIN, "status").allowed is True
    assert guard.consume_command(ADMIN, "status").allowed is True
    limited = guard.consume_command(ADMIN, "status")
    assert limited.reason is AuthDenial.RATE_LIMITED
    assert limited.retry_after == 60

    assert guard.consume_vote(OTHER).allowed is True
    assert guard.consume_vote(OTHER).allowed is True
    assert guard.consume_vote(OTHER).reason is AuthDenial.RATE_LIMITED

    sendnow_guard = AdminGuard(admin_user_ids=frozenset({ADMIN}), clock=clock, sendnow_per_minute=1)
    assert sendnow_guard.consume_command(ADMIN, "sendnow").allowed is True
    assert sendnow_guard.consume_command(ADMIN, "sendnow").reason is AuthDenial.RATE_LIMITED


def test_command_name_and_messages() -> None:
    assert command_name("/Start@bot") == "start"
    assert command_name("hello") is None
    assert command_name("/") is None
    assert rate_limit_message(5) == "Too many commands. Try again in 5 s."
    assert vote_rate_limit_message(5) == "Too many votes. Try again in 5 s."


def test_admin_filter_uses_message_authorization() -> None:
    guard = AdminGuard(admin_user_ids=frozenset({ADMIN}), clock=FakeClock())
    admin_filter = AdminFilter(guard)

    assert admin_filter.filter(_message_update()) is True  # type: ignore[arg-type]
    assert admin_filter.filter(_message_update(user_id=OTHER)) is False  # type: ignore[arg-type]
