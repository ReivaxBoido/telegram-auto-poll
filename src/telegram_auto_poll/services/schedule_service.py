"""Schedule lifecycle (FR-121 - FR-124).

``next_run_at`` is always derived from the pure schedule mathematics, never incremented by hand, so
pausing, resuming, and editing cannot drift from the recurrence rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from ..db.uow import UnitOfWork
from ..domain.entities import AuditEvent, Schedule
from ..domain.enums import AuditAction
from ..domain.errors import ConflictError, NotFoundError, ValidationError
from ..domain.ports import Clock
from ..domain.schedule_math import next_occurrence, next_occurrences
from .pagination import PAGE_SIZE, Page, offset_for
from .poll_service import ScheduleDraft, ScheduleView


@dataclass(frozen=True, slots=True)
class DeletedSchedule:
    """The removed schedule's identity, for the confirmation message."""

    schedule_id: int
    poll_id: int


class ScheduleService:
    """Creates, pauses, resumes, previews, and deletes schedules."""

    def __init__(self, *, uow: UnitOfWork, clock: Clock, page_size: int = PAGE_SIZE) -> None:
        self._uow = uow
        self._clock = clock
        self._page_size = page_size

    async def create(
        self,
        *,
        poll_id: int,
        actor_user_id: int,
        draft: ScheduleDraft,
    ) -> Schedule:
        """Create a schedule for an active poll.

        Raises:
            NotFoundError: the poll does not exist.
            ValidationError: the poll is archived, or the recurrence rule is invalid.
        """
        now = self._clock.now()
        async with self._uow() as repos:
            poll = await repos.polls.get(poll_id)
            if poll is None:
                raise NotFoundError(f"poll {poll_id} does not exist")
            if poll.is_archived:
                raise ValidationError("poll", "an archived poll cannot be scheduled")

            candidate = Schedule.create(
                poll_id=poll_id,
                kind=draft.kind,
                timezone=draft.timezone,
                time_of_day=draft.time_of_day,
                weekdays=draft.weekdays,
                day_of_month=draft.day_of_month,
                run_at=draft.run_at,
                start_date=draft.start_date,
                end_date=draft.end_date,
                now=now,
            )
            stored = await repos.schedules.add(candidate, now=now)
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.SCHEDULE_CREATE,
                    entity_type="schedule",
                    entity_id=stored.id or 0,
                    details={"poll_id": poll_id, "kind": stored.kind.value},
                    created_at=now,
                ),
                now=now,
            )
        return stored

    async def pause(self, schedule_id: int, *, actor_user_id: int) -> Schedule:
        """Pause a schedule and clear its next run (I-7).

        Raises:
            NotFoundError: the schedule does not exist.
        """
        now = self._clock.now()
        async with self._uow() as repos:
            schedule = await repos.schedules.get(schedule_id)
            if schedule is None:
                raise NotFoundError(f"schedule {schedule_id} does not exist")
            if schedule.is_paused:
                return schedule

            paused = await repos.schedules.set_paused(
                schedule_id, paused=True, next_run_at=None, now=now
            )
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.SCHEDULE_PAUSE,
                    entity_type="schedule",
                    entity_id=schedule_id,
                    details={"poll_id": schedule.poll_id},
                    created_at=now,
                ),
                now=now,
            )
        return cast("Schedule", paused)

    async def resume(self, schedule_id: int, *, actor_user_id: int) -> Schedule:
        """Resume a schedule, recomputing the next run from now (FR-124).

        Raises:
            NotFoundError: the schedule does not exist.
            ConflictError: the recurrence rule has no future occurrence (expired one-off).
        """
        now = self._clock.now()
        async with self._uow() as repos:
            schedule = await repos.schedules.get(schedule_id)
            if schedule is None:
                raise NotFoundError(f"schedule {schedule_id} does not exist")

            active = schedule
            if schedule.is_paused:
                active = Schedule(
                    poll_id=schedule.poll_id,
                    kind=schedule.kind,
                    timezone=schedule.timezone,
                    time_of_day=schedule.time_of_day,
                    weekdays=schedule.weekdays,
                    day_of_month=schedule.day_of_month,
                    run_at=schedule.run_at,
                    start_date=schedule.start_date,
                    end_date=schedule.end_date,
                    is_paused=False,
                    last_run_at=schedule.last_run_at,
                    id=schedule.id,
                    created_at=schedule.created_at,
                )

            upcoming = next_occurrence(active, after=now)
            if upcoming is None:
                raise ConflictError("this schedule has no future occurrence to resume")

            resumed = await repos.schedules.set_paused(
                schedule_id, paused=False, next_run_at=upcoming, now=now
            )
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.SCHEDULE_RESUME,
                    entity_type="schedule",
                    entity_id=schedule_id,
                    details={"next_run_at": upcoming.isoformat()},
                    created_at=now,
                ),
                now=now,
            )
        return cast("Schedule", resumed)

    async def delete(self, schedule_id: int, *, actor_user_id: int) -> DeletedSchedule:
        """Hard-delete a schedule, leaving polls, chats, and delivery history untouched.

        Raises:
            NotFoundError: the schedule does not exist.
        """
        now = self._clock.now()
        async with self._uow() as repos:
            schedule = await repos.schedules.get(schedule_id)
            if schedule is None:
                raise NotFoundError(f"schedule {schedule_id} does not exist")
            await repos.schedules.delete(schedule_id)
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.SCHEDULE_DELETE,
                    entity_type="schedule",
                    entity_id=schedule_id,
                    details={"poll_id": schedule.poll_id, "kind": schedule.kind.value},
                    created_at=now,
                ),
                now=now,
            )
        return DeletedSchedule(schedule_id=schedule_id, poll_id=schedule.poll_id)

    async def preview(self, schedule_id: int, *, count: int = 5) -> tuple[datetime, ...]:
        """Return the next occurrences, computed from now.

        Raises:
            NotFoundError: the schedule does not exist.
        """
        schedule = await self.get(schedule_id)
        return tuple(next_occurrences(schedule, after=self._clock.now(), count=count))

    async def get(self, schedule_id: int) -> Schedule:
        """Load one schedule.

        Raises:
            NotFoundError: the schedule does not exist.
        """
        async with self._uow() as repos:
            schedule = await repos.schedules.get(schedule_id)
        if schedule is None:
            raise NotFoundError(f"schedule {schedule_id} does not exist")
        return schedule

    async def detail(self, schedule_id: int) -> ScheduleView:
        """Return the schedule with its next five occurrences."""
        schedule = await self.get(schedule_id)
        runs = tuple(next_occurrences(schedule, after=self._clock.now(), count=5))
        return ScheduleView(schedule=schedule, next_runs=runs)

    async def page(self, number: int, *, poll_id: int | None = None) -> Page[Schedule]:
        """Return one page of schedules, oldest first."""
        offset = offset_for(number, self._page_size)
        async with self._uow() as repos:
            total = await repos.schedules.count(poll_id=poll_id)
            items = await repos.schedules.list_all(
                poll_id=poll_id, limit=self._page_size, offset=offset
            )
        return Page.create(items, number=number, size=self._page_size, total=total)
