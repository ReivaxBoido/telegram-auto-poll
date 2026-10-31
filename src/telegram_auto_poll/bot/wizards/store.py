"""In-memory wizard sessions with a TTL (FR-111).

Wizard state is deliberately not persisted: a restart drops half-finished polls rather than reviving
them against a changed chat list. Expiry is lazy on read plus a periodic ``expire()`` for the janitor.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...domain.ports import Clock
from .states import WizardState

DEFAULT_TTL_SECONDS = 900


@dataclass(frozen=True, slots=True)
class _Session:
    state: WizardState
    touched_at: float


class WizardStore:
    """One active session per admin, evicted after ``ttl_seconds`` of inactivity."""

    def __init__(self, *, clock: Clock, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[int, _Session] = {}

    def put(self, state: WizardState) -> None:
        """Store or replace the admin's session and refresh its timestamp."""
        self._sessions[state.admin_user_id] = _Session(
            state=state, touched_at=self._clock.now().timestamp()
        )

    def get(self, admin_user_id: int) -> WizardState | None:
        """Return the session, discarding it first if it has expired."""
        session = self._sessions.get(admin_user_id)
        if session is None:
            return None
        if self._expired(session):
            del self._sessions[admin_user_id]
            return None
        return session.state

    def discard(self, admin_user_id: int) -> None:
        """Remove a session if present."""
        self._sessions.pop(admin_user_id, None)

    def expire(self) -> int:
        """Drop every expired session; return how many were removed."""
        stale = [key for key, session in self._sessions.items() if self._expired(session)]
        for key in stale:
            del self._sessions[key]
        return len(stale)

    def active_count(self) -> int:
        """Return the number of sessions, expired or not."""
        return len(self._sessions)

    def _expired(self, session: _Session) -> bool:
        age = self._clock.now().timestamp() - session.touched_at
        return age >= self._ttl_seconds
