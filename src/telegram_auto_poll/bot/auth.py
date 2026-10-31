"""Admin authorization, callback validation, and the per-admin command budget (FR-106 - FR-108).

Identity is decided from the update alone, never from wizard state. The checks are split so a handler
can answer a refusal precisely:

1. ``authorize_message`` / ``authorize_callback`` — private chat, allowlist, callback namespace.
2. ``consume_command`` — the per-admin token bucket (plus a separate ``/sendnow`` bucket).

Nothing here mutates or persists anything, so a refusal cannot leave a trace.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from telegram import Update
from telegram.ext import filters

from ..domain.ports import Clock

CALLBACK_VERSION = "v1"

#: Callback namespaces the codec may produce: poll, schedule, wizard (spec §8.3).
CALLBACK_NAMESPACES = frozenset({"p", "s", "w"})

COMMAND_WINDOW_SECONDS = 60.0
SENDNOW_COMMAND = "sendnow"
SENDNOW_PER_MINUTE = 5

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


class AdminGuard:
    """Checks identity and enforces the per-admin command budgets."""

    def __init__(
        self,
        *,
        admin_user_ids: frozenset[int],
        clock: Clock,
        commands_per_minute: int = 20,
        sendnow_per_minute: int = SENDNOW_PER_MINUTE,
        namespaces: frozenset[str] = CALLBACK_NAMESPACES,
    ) -> None:
        self._admins = admin_user_ids
        self._clock = clock
        self._commands_per_minute = commands_per_minute
        self._sendnow_per_minute = sendnow_per_minute
        self._namespaces = namespaces
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

    def authorize_callback(self, update: object) -> Decision:
        """Re-authorize the callback actor and validate the data envelope."""
        query = getattr(update, "callback_query", None)
        if query is None:
            return Decision(allowed=False, reason=AuthDenial.BAD_CALLBACK)
        user_id = getattr(getattr(query, "from_user", None), "id", None)
        if user_id is None or user_id not in self._admins:
            return Decision(allowed=False, reason=AuthDenial.NOT_ADMIN)

        data = getattr(query, "data", None)
        parts = data.split(":") if isinstance(data, str) else []
        if len(parts) < 3 or parts[0] != CALLBACK_VERSION or parts[1] not in self._namespaces:
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
    """PTB filter: pass only private-chat messages from allowlisted users.

    Rate limiting stays in the handler, so a refused command can be answered with its retry-after
    instead of being silently dropped.
    """

    def __init__(self, guard: AdminGuard) -> None:
        super().__init__()
        self._guard = guard

    def filter(self, update: Update) -> bool:
        return self._guard.authorize_message(update).allowed
