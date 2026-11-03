"""Publishing a poll to one or more registered chats (SPEC-003 FR-309, FR-310).

One publish creates one open instance and one pending message per target in a single transaction.
The posting worker sends them; no Telegram call happens while the transaction is open.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from ..db.uow import UnitOfWork
from ..domain.entities import AuditEvent, Poll, PollInstance, PollMessage
from ..domain.enums import AuditAction, InstanceTrigger, PollStatus
from ..domain.errors import NotFoundError, ValidationError
from ..domain.ports import Clock


@dataclass(frozen=True, slots=True)
class PublishResult:
    """What one publish created."""

    poll: Poll
    instance: PollInstance
    messages: tuple[PollMessage, ...]


class PublishService:
    """Creates instances and their pending messages."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        clock: Clock,
        wake: Callable[[], None] | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._wake = wake

    async def publish(
        self,
        *,
        actor_user_id: int,
        poll_id: int,
        chat_ids: Iterable[int],
    ) -> PublishResult:
        """Publish ``poll_id`` to the selected active chats.

        Raises:
            NotFoundError: the poll does not exist or is deleted.
            ValidationError: no target, or a target is not registered/active.
        """
        targets = tuple(sorted(set(chat_ids)))
        if not targets:
            raise ValidationError("targets", "select at least one active chat")

        now = self._clock.now()
        async with self._uow() as repos:
            poll = await repos.polls.get(poll_id)
            if poll is None or poll.status is PollStatus.DELETED:
                raise NotFoundError(f"poll {poll_id} does not exist")
            active = await repos.chats.active_ids()
            unavailable = sorted(set(targets) - active)
            if unavailable:
                raise ValidationError(
                    "targets",
                    f"chat {unavailable[0]} is not registered or is inactive",
                )

            if poll.status is PollStatus.DRAFT:
                await repos.polls.set_status(poll_id, PollStatus.ACTIVE, now=now)
                poll = replace(poll, status=PollStatus.ACTIVE)

            instance = await repos.instances.add(
                PollInstance(
                    poll_id=poll_id,
                    trigger=InstanceTrigger.MANUAL,
                    created_by=actor_user_id,
                    created_at=now,
                ),
                now=now,
            )
            messages = await repos.messages.add_many(
                instance.id or 0,
                list(targets),
                now=now,
            )
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.POLL_PUBLISH.value,
                    entity_type="poll_instance",
                    entity_id=instance.id or 0,
                    details={"poll_id": poll_id, "targets": list(targets)},
                    created_at=now,
                ),
                now=now,
            )

        if self._wake is not None:
            self._wake()
        return PublishResult(
            poll=poll,
            instance=instance,
            messages=tuple(messages),
        )
