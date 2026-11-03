"""Admin and participant authorization (SPEC-003 FR-318, FR-319, ADR-0004).

Two paths are deliberately separate:

1. Admin commands and admin callbacks: private chat + ``ADMIN_USER_IDS``.
2. Participant votes: any callback actor, validated later against the stored message/instance/option.

Callback data is never identity. A forged vote can only change the sender's own response.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from telegram import Update
from telegram.ext import filters

from ..domain.callbacks import CallbackDataError, decode
from ..domain.ports import Clock

CALLBACK_VERSION = "v1"
ADMIN_NAMESPACES = frozenset({"p", "t", "w", "c"})
VOTE_NAMESPACE = "v"
CALLBACK_NAMESPACES = ADMIN_NAMESPACES | {VOTE_NAMESPACE}

COMMAND_WINDOW_SECONDS = 60.0
SENDNOW_COMMAND = "sendnow"
SENDNOW_PER_MINUTE = 5
DEFAULT_VOTES_PER_MINUTE = 30

GENERIC_REFUSAL = "This is a private bot. Access is restricted to its administrators."


class AuthDenial(StrEnum):
    """Why a request was refused."""

    NOT_PRIVATE = "not_private"
    NOT_ADMIN = "not_admin"
    RATE_LIMITED = "rate_limited"
    BAD_CALLBACK = "bad_callback"


@dataclass(frozen=True, slots=True)
class Decision:
    """The verdict for one message or callback."""

    allowed: bool
    reason: AuthDenial | None = None
    retry_after: int | None = None


ALLOWED = Decision(allowed=True)


def command_name(text: str | None) -> str | None:
    """Return the lower-case command name from a message, ignoring ``@botname`` suffixes."""
    if not text or not text.startswith("/"):
        return None
    body = text[1:].strip()
    if not body:
        return None
    token = body.split(maxsplit=1)[0]
    name = token.split("@", 1)[0].strip().lower()
    return name or None


def rate_limit_message(retry_after: int) -> str:
    """Render the refusal shown when the command budget is exhausted."""
    return f"Too many commands. Try again in {retry_after} s."


def vote_rate_limit_message(retry_after: int) -> str:
    """Render the refusal shown when the vote budget is exhausted."""
    return f"Too many votes. Try again in {retry_after} s."


def _decoded_namespace(update: object) -> str | None:
    query = getattr(update, "callback_query", None)
    data = getattr(query, "data", None)
    if not isinstance(data, str):
        return None
    try:
        return decode(data).namespace
    except CallbackDataError:
        return None


class AdminGuard:
    """Checks identity and enforces the per-admin and per-user vote budgets."""

    def __init__(
        self,
        *,
        admin_user_ids: frozenset[int],
        clock: Clock,
        commands_per_minute: int = 20,
        sendnow_per_minute: int = SENDNOW_PER_MINUTE,
        votes_per_minute: int = DEFAULT_VOTES_PER_MINUTE,
    ) -> None:
        self._admins = admin_user_ids
        self._clock = clock
        self._commands_per_minute = commands_per_minute
        self._sendnow_per_minute = sendnow_per_minute
        self._votes_per_minute = votes_per_minute
        self._usage: dict[tuple[int, str], deque[datetime]] = {}

    # ----------------------------------------------------------------------------------------
    # Identity
    # ----------------------------------------------------------------------------------------

    def authorize_message(self, update: object) -> Decision:
        """Accept only private-chat messages from allowlisted users."""
        message = getattr(update, "message", None)
        if message is None:
            return Decision(allowed=False, reason=AuthDenial.NOT_PRIVATE)
        chat = getattr(message, "chat", None)
        if str(getattr(chat, "type", "")).lower() != "private":
            return Decision(allowed=False, reason=AuthDenial.NOT_PRIVATE)
        user_id = getattr(getattr(message, "from_user", None), "id", None)
        if user_id is None or user_id not in self._admins:
            return Decision(allowed=False, reason=AuthDenial.NOT_ADMIN)
        return ALLOWED

    def authorize_admin_callback(self, update: object) -> Decision:
        """Accept an admin callback only from a private chat and an allowlisted actor."""
        query = getattr(update, "callback_query", None)
        if query is None:
            return Decision(allowed=False, reason=AuthDenial.BAD_CALLBACK)
        user_id = getattr(getattr(query, "from_user", None), "id", None)
        if user_id is None or user_id not in self._admins:
            return Decision(allowed=False, reason=AuthDenial.NOT_ADMIN)
        message = getattr(query, "message", None)
        chat = getattr(message, "chat", None)
        if str(getattr(chat, "type", "")).lower() != "private":
            return Decision(allowed=False, reason=AuthDenial.NOT_PRIVATE)
        namespace = _decoded_namespace(update)
        if namespace not in ADMIN_NAMESPACES:
            return Decision(allowed=False, reason=AuthDenial.BAD_CALLBACK)
        return ALLOWED

    def authorize_vote_callback(self, update: object) -> Decision:
        """Accept a vote callback from any actor; the response service validates the message."""
        query = getattr(update, "callback_query", None)
        if query is None:
            return Decision(allowed=False, reason=AuthDenial.BAD_CALLBACK)
        user_id = getattr(getattr(query, "from_user", None), "id", None)
        if user_id is None:
            return Decision(allowed=False, reason=AuthDenial.BAD_CALLBACK)
        if _decoded_namespace(update) != VOTE_NAMESPACE:
            return Decision(allowed=False, reason=AuthDenial.BAD_CALLBACK)
        return ALLOWED

    # ----------------------------------------------------------------------------------------
    # Budget
    # ----------------------------------------------------------------------------------------

    def consume_command(self, user_id: int, command: str | None) -> Decision:
        """Charge one command to ``user_id``, or refuse with the seconds still to wait."""
        now = self._clock.now()
        wait = self._wait(user_id, "commands", self._commands_per_minute, now)
        sendnow_wait = 0.0
        if command == SENDNOW_COMMAND:
            sendnow_wait = self._wait(user_id, "sendnow", self._sendnow_per_minute, now)

        longest = max(wait, sendnow_wait)
        if longest > 0:
            return Decision(
                allowed=False,
                reason=AuthDenial.RATE_LIMITED,
                retry_after=max(math.ceil(longest), 1),
            )

        self._record(user_id, "commands", now)
        if command == SENDNOW_COMMAND:
            self._record(user_id, "sendnow", now)
        return ALLOWED

    def consume_vote(self, user_id: int) -> Decision:
        """Charge one vote to ``user_id``, or refuse with the seconds still to wait."""
        now = self._clock.now()
        wait = self._wait(user_id, "votes", self._votes_per_minute, now)
        if wait > 0:
            return Decision(
                allowed=False,
                reason=AuthDenial.RATE_LIMITED,
                retry_after=max(math.ceil(wait), 1),
            )
        self._record(user_id, "votes", now)
        return ALLOWED

    def _wait(self, user_id: int, bucket: str, limit: int, now: datetime) -> float:
        stamps = self._usage.get((user_id, bucket))
        if not stamps:
            return 0.0
        horizon = COMMAND_WINDOW_SECONDS
        while stamps and (now - stamps[0]).total_seconds() >= horizon:
            stamps.popleft()
        if len(stamps) < limit:
            return 0.0
        oldest_counted = stamps[len(stamps) - limit]
        return horizon - (now - oldest_counted).total_seconds()

    def _record(self, user_id: int, bucket: str, now: datetime) -> None:
        self._usage.setdefault((user_id, bucket), deque()).append(now)


class AdminFilter(filters.BaseFilter):
    """PTB filter: pass only private-chat messages from allowlisted users."""

    def __init__(self, guard: AdminGuard) -> None:
        super().__init__()
        self._guard = guard

    def filter(self, update: Update) -> bool:
        return self._guard.authorize_message(update).allowed
