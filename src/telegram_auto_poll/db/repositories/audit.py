"""Audit repository: append-only mutation records (FR-134).

Audit rows are written in the same transaction as the mutation they describe, so a rollback removes
both. There is deliberately no update method.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import AuditEvent
from ...domain.enums import AuditAction
from ..models import AuditEventRow
from .common import dumps, from_epoch, load_object, rowcount, to_epoch


def _to_event(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        actor_user_id=row.actor_user_id,
        action=AuditAction(row.action),
        entity_type=row.entity_type,
        entity_id=row.entity_id or 0,
        details=load_object(row.details_json),
        id=row.id,
        created_at=from_epoch(row.created_at),
    )


class AuditRepository:
    """Appends and reads ``audit_events``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: AuditEvent, *, now: datetime) -> AuditEvent:
        """Append one immutable event; ``details`` must already be free of secrets."""
        row = AuditEventRow(
            actor_user_id=event.actor_user_id,
            action=event.action.value,
            entity_type=event.entity_type,
            entity_id=event.entity_id or None,
            details_json=None if event.details is None else dumps(event.details),
            created_at=to_epoch(event.created_at or now),
        )
        self._session.add(row)
        await self._session.flush()
        return _to_event(row)

    async def list_recent(self, *, limit: int = 20) -> list[AuditEvent]:
        """Return the newest events first."""
        statement = select(AuditEventRow).order_by(AuditEventRow.id.desc()).limit(limit)
        rows = (await self._session.scalars(statement)).all()
        return [_to_event(row) for row in rows]

    async def list_for_entity(self, entity_type: str, entity_id: int) -> list[AuditEvent]:
        """Return the history of one entity, oldest first."""
        statement = (
            select(AuditEventRow)
            .where(AuditEventRow.entity_type == entity_type)
            .where(AuditEventRow.entity_id == entity_id)
            .order_by(AuditEventRow.id)
        )
        rows = (await self._session.scalars(statement)).all()
        return [_to_event(row) for row in rows]

    async def delete_batch_before(self, cutoff: datetime, *, limit: int) -> int:
        """Delete at most ``limit`` events older than ``cutoff`` (FR-135 batching)."""
        stamp = to_epoch(cutoff)
        stale_ids = (
            select(AuditEventRow.id)
            .where(AuditEventRow.created_at < stamp)
            .order_by(AuditEventRow.id)
            .limit(limit)
        )
        statement = delete(AuditEventRow).where(AuditEventRow.id.in_(stale_ids))
        result = await self._session.execute(statement)
        return rowcount(result)
