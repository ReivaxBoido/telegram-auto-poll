"""Audit records (FR-134).

Audit rows must share the transaction of the mutation they describe. ``record`` therefore takes the
repositories of the caller's unit of work instead of opening its own session.
"""

from __future__ import annotations

from datetime import datetime

from ..db.uow import Repositories, UnitOfWork
from ..domain.entities import AuditEvent
from ..domain.enums import AuditAction


class AuditService:
    """Builds, writes, and reads audit events."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def record(
        self,
        repos: Repositories,
        *,
        actor_user_id: int,
        action: AuditAction,
        entity_type: str,
        entity_id: int,
        now: datetime,
        details: dict[str, object] | None = None,
    ) -> AuditEvent:
        """Append an event inside the caller's transaction.

        The caller passes the bundles it is already mutating in; if that transaction rolls back,
        this row disappears with it.
        """
        return await repos.audit.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
                created_at=now,
            ),
            now=now,
        )

    async def recent(self, *, limit: int = 20) -> list[AuditEvent]:
        """Return the newest events first."""
        async with self._uow() as repos:
            return await repos.audit.list_recent(limit=limit)

    async def history(self, entity_type: str, entity_id: int) -> list[AuditEvent]:
        """Return one entity's history, oldest first."""
        async with self._uow() as repos:
            return await repos.audit.list_for_entity(entity_type, entity_id)
