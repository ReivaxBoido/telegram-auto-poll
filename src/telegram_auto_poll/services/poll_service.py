"""Poll lifecycle, listing, and response inspection (SPEC-003 FR-306 - FR-316)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil

from ..db.uow import Repositories, UnitOfWork
from ..domain.entities import (
    AuditEvent,
    BroadcastResult,
    Poll,
    PollInstance,
    PollOption,
)
from ..domain.enums import AuditAction, InstanceStatus, PollStatus, SkipReason
from ..domain.errors import NotFoundError, ValidationError
from ..domain.ports import Clock
from .broadcaster import ResponseBroadcaster
from .pagination import PAGE_SIZE, Page, offset_for

RESPONSE_PAGE_SIZE = 20


@dataclass(frozen=True, slots=True)
class PollSummary:
    """One row of ``/polls``."""

    poll: Poll
    latest_instance: PollInstance | None
    response_count: int


@dataclass(frozen=True, slots=True)
class PollDetail:
    """The detail screen for one poll."""

    poll: Poll
    latest_instance: PollInstance | None
    counts: dict[int, int]
    total_respondents: int


@dataclass(frozen=True, slots=True)
class ResponsePage:
    """One page of names for one option."""

    poll: Poll
    instance: PollInstance
    option: PollOption
    names: tuple[str, ...]
    number: int
    pages: int
    total: int


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """What ``/refresh`` retried and re-rendered."""

    instance_id: int
    retried: int
    broadcast: BroadcastResult


class PollService:
    """Creates and manages polls, instances, and response views."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        clock: Clock,
        broadcaster: ResponseBroadcaster,
        page_size: int = PAGE_SIZE,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._broadcaster = broadcaster
        self._page_size = page_size

    async def create(
        self,
        *,
        actor_user_id: int,
        title: str,
        options: tuple[str, ...] | list[str],
    ) -> Poll:
        """Validate and persist a draft poll with its options and audit event."""
        now = self._clock.now()
        candidate = Poll.create(
            title=title,
            options=options,
            created_by=actor_user_id,
            now=now,
        )
        async with self._uow() as repos:
            stored = await repos.polls.add(candidate, now=now)
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.POLL_CREATE.value,
                    entity_type="poll",
                    entity_id=stored.id or 0,
                    details={"title": stored.title, "option_count": len(stored.options)},
                    created_at=now,
                ),
                now=now,
            )
        return stored

    async def page(self, number: int) -> Page[PollSummary]:
        """Return one page of polls with their latest instance and response count."""
        offset = offset_for(number, self._page_size)
        async with self._uow() as repos:
            total = await repos.polls.count()
            polls = await repos.polls.list_all(limit=self._page_size, offset=offset)
            summaries = [
                PollSummary(
                    poll=poll,
                    latest_instance=await repos.instances.latest_for_poll(poll.id or 0),
                    response_count=await self._response_count(repos, poll.id or 0),
                )
                for poll in polls
            ]
        return Page.create(summaries, number=number, total=total, size=self._page_size)

    async def detail(self, poll_id: int) -> PollDetail:
        """Return a poll with its latest instance and per-option counts."""
        async with self._uow() as repos:
            poll = await self._require_poll(repos, poll_id)
            latest = await repos.instances.latest_for_poll(poll_id)
            counts = (
                await repos.responses.counts_by_option(latest.id or 0) if latest is not None else {}
            )
            total = (
                await repos.responses.count_for_instance(latest.id or 0)
                if latest is not None
                else 0
            )
        return PollDetail(
            poll=poll,
            latest_instance=latest,
            counts=counts,
            total_respondents=total,
        )

    async def close(self, poll_id: int, *, actor_user_id: int) -> PollInstance:
        """Close the latest open instance and skip its pending messages."""
        now = self._clock.now()
        async with self._uow() as repos:
            poll = await self._require_poll(repos, poll_id)
            instance = await repos.instances.latest_with_status(poll_id, InstanceStatus.OPEN)
            if instance is None:
                raise NotFoundError(f"poll {poll_id} has no open instance")
            await repos.instances.set_status(
                instance.id or 0,
                InstanceStatus.CLOSED,
                now=now,
                closed_at=now,
            )
            await repos.messages.skip_pending_for_instance(
                instance.id or 0,
                SkipReason.INSTANCE_CLOSED.value,
                now=now,
            )
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.POLL_CLOSE.value,
                    entity_type="poll_instance",
                    entity_id=instance.id or 0,
                    details={"poll_id": poll.id},
                    created_at=now,
                ),
                now=now,
            )
            closed = replace(instance, status=InstanceStatus.CLOSED, closed_at=now)
        await self._broadcaster.refresh(closed.id or 0)
        return closed

    async def reopen(self, poll_id: int, *, actor_user_id: int) -> PollInstance:
        """Reopen the latest closed instance and restore its vote keyboard."""
        now = self._clock.now()
        async with self._uow() as repos:
            poll = await self._require_poll(repos, poll_id)
            instance = await repos.instances.latest_with_status(poll_id, InstanceStatus.CLOSED)
            if instance is None:
                raise NotFoundError(f"poll {poll_id} has no closed instance")
            await repos.instances.set_status(
                instance.id or 0,
                InstanceStatus.OPEN,
                now=now,
                closed_at=None,
            )
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.POLL_REOPEN.value,
                    entity_type="poll_instance",
                    entity_id=instance.id or 0,
                    details={"poll_id": poll.id},
                    created_at=now,
                ),
                now=now,
            )
            reopened = replace(instance, status=InstanceStatus.OPEN, closed_at=None)
        await self._broadcaster.refresh(reopened.id or 0)
        return reopened

    async def delete(self, poll_id: int, *, actor_user_id: int) -> BroadcastResult:
        """Soft-delete a poll, remove its responses, and redact every message."""
        now = self._clock.now()
        async with self._uow() as repos:
            await self._require_poll(repos, poll_id)
            instances = await repos.instances.list_for_poll(poll_id)
            instance_ids = [instance.id or 0 for instance in instances]
            await repos.polls.set_status(poll_id, PollStatus.DELETED, now=now)
            for instance in instances:
                await repos.instances.set_status(
                    instance.id or 0,
                    InstanceStatus.DELETED,
                    now=now,
                    closed_at=instance.closed_at,
                )
                await repos.messages.skip_pending_for_instance(
                    instance.id or 0,
                    SkipReason.POLL_DELETED.value,
                    now=now,
                )
            await repos.responses.delete_for_instances(instance_ids)
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.POLL_DELETE.value,
                    entity_type="poll",
                    entity_id=poll_id,
                    details={"instance_count": len(instances)},
                    created_at=now,
                ),
                now=now,
            )
        return await self._broadcaster.redact(instance_ids)

    async def refresh(self, poll_id: int, *, actor_user_id: int) -> RefreshResult:
        """Retry failed messages and re-render every sent message of the latest instance."""
        now = self._clock.now()
        async with self._uow() as repos:
            poll = await self._require_poll(repos, poll_id)
            instance = await repos.instances.latest_for_poll(poll_id)
            if instance is None:
                raise NotFoundError(f"poll {poll_id} has not been published")
            retried = await repos.messages.reset_failed_for_instance(instance.id or 0, now=now)
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.POLL_REFRESH.value,
                    entity_type="poll_instance",
                    entity_id=instance.id or 0,
                    details={"poll_id": poll.id, "retried": retried},
                    created_at=now,
                ),
                now=now,
            )
        broadcast = await self._broadcaster.refresh(instance.id or 0)
        return RefreshResult(
            instance_id=instance.id or 0,
            retried=retried,
            broadcast=broadcast,
        )

    async def responses(
        self,
        poll_id: int,
        *,
        option_position: int = 1,
        page: int = 1,
    ) -> ResponsePage:
        """Return one page of names for the selected option of the latest instance."""
        async with self._uow() as repos:
            poll = await self._require_poll(repos, poll_id)
            instance = await repos.instances.latest_for_poll(poll_id)
            if instance is None:
                raise NotFoundError(f"poll {poll_id} has not been published")
            if not 1 <= option_position <= len(poll.options):
                raise ValidationError(
                    "option",
                    f"option must be between 1 and {len(poll.options)}",
                )
            option = poll.options[option_position - 1]
            responses = await repos.responses.list_for_instance(instance.id or 0)

        names = tuple(
            response.first_name for response in responses if response.option_id == option.id
        )
        total = len(names)
        pages = max(ceil(total / RESPONSE_PAGE_SIZE), 1)
        if page < 1 or page > pages:
            raise ValidationError("page", f"page must be between 1 and {pages}")
        start = (page - 1) * RESPONSE_PAGE_SIZE
        return ResponsePage(
            poll=poll,
            instance=instance,
            option=option,
            names=names[start : start + RESPONSE_PAGE_SIZE],
            number=page,
            pages=pages,
            total=total,
        )

    async def _response_count(self, repos: Repositories, poll_id: int) -> int:
        latest = await repos.instances.latest_for_poll(poll_id)
        if latest is None:
            return 0
        return await repos.responses.count_for_instance(latest.id or 0)

    async def _require_poll(self, repos: Repositories, poll_id: int) -> Poll:
        poll = await repos.polls.get(poll_id)
        if poll is None or poll.status is PollStatus.DELETED:
            raise NotFoundError(f"poll {poll_id} does not exist")
        return poll
