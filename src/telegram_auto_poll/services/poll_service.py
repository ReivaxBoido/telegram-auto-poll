"""Poll creation, listing, and detail (FR-116, FR-119).

A poll, its targets, and an optional first schedule are committed in one transaction, together with
their audit events (FR-116, FR-134). Validation runs before anything is written; a failure after the
insert therefore rolls the poll back rather than leaving a half-created definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import cast

from ..db.uow import UnitOfWork
from ..domain.entities import AuditEvent, Poll, Schedule
from ..domain.enums import AuditAction, ScheduleKind
from ..domain.errors import NotFoundError, ValidationError
from ..domain.ports import Clock
from ..domain.schedule_math import next_occurrence, next_occurrences
from .pagination import PAGE_SIZE, Page, offset_for


@dataclass(frozen=True, slots=True)
class ScheduleDraft:
    """The schedule fields the wizard collected, before validation by ``Schedule.create``."""

    kind: ScheduleKind
    timezone: str
    time_of_day: time | None = None
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None
    run_at: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True, slots=True)
class CreatedPoll:
    """What a successful wizard confirmation produced."""

    poll: Poll
    schedule: Schedule | None


@dataclass(frozen=True, slots=True)
class ScheduleView:
    """A schedule with the occurrences the detail screens display (FR-123)."""

    schedule: Schedule
    next_runs: tuple[datetime, ...]


@dataclass(frozen=True, slots=True)
class PollDetail:
    poll: Poll
    schedules: tuple[ScheduleView, ...]


class PollService:
    """Creates and reads poll definitions."""

    def __init__(self, *, uow: UnitOfWork, clock: Clock, page_size: int = PAGE_SIZE) -> None:
        self._uow = uow
        self._clock = clock
        self._page_size = page_size

    async def create(
        self,
        *,
        actor_user_id: int,
        question: str,
        options: tuple[str, ...] | list[str],
        target_chat_ids: tuple[int, ...] | list[int],
        description: str | None = None,
        is_anonymous: bool = True,
        allows_multiple_answers: bool = False,
        schedule: ScheduleDraft | None = None,
    ) -> CreatedPoll:
        """Persist a poll, its targets, and an optional schedule atomically.

        Raises:
            ValidationError: invalid content, no target, an inactive target, or an invalid schedule.
        """
        now = self._clock.now()
        targets = tuple(sorted(set(target_chat_ids)))
        if not targets:
            raise ValidationError("targets", "select at least one active chat")
        poll = Poll.create(
            question=question,
            options=options,
            description=description,
            is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple_answers,
            targets=targets,
            now=now,
        )

        async with self._uow() as repos:
            active = await repos.chats.active_ids()
            unavailable = sorted(set(targets) - active)
            if unavailable:
                raise ValidationError(
                    "targets",
                    f"chat {unavailable[0]} is not registered or is inactive",
                )

            stored = await repos.polls.add(poll, now=now)
            await repos.audit.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    action=AuditAction.POLL_CREATE,
                    entity_type="poll",
                    entity_id=stored.id or 0,
                    details={
                        "question": stored.question,
                        "option_count": len(stored.options),
                        "targets": list(stored.targets),
                    },
                    created_at=now,
                ),
                now=now,
            )

            stored_schedule: Schedule | None = None
            if schedule is not None:
                candidate = Schedule.create(
                    poll_id=cast("int", stored.id),
                    kind=schedule.kind,
                    timezone=schedule.timezone,
                    time_of_day=schedule.time_of_day,
                    weekdays=schedule.weekdays,
                    day_of_month=schedule.day_of_month,
                    run_at=schedule.run_at,
                    start_date=schedule.start_date,
                    end_date=schedule.end_date,
                    now=now,
                )
                stored_schedule = await repos.schedules.add(candidate, now=now)
                await repos.audit.add(
                    AuditEvent(
                        actor_user_id=actor_user_id,
                        action=AuditAction.SCHEDULE_CREATE,
                        entity_type="schedule",
                        entity_id=stored_schedule.id or 0,
                        details={
                            "poll_id": stored.id,
                            "kind": stored_schedule.kind.value,
                            "timezone": stored_schedule.timezone,
                        },
                        created_at=now,
                    ),
                    now=now,
                )

        return CreatedPoll(poll=stored, schedule=stored_schedule)

    async def page(self, number: int, *, active_only: bool = True) -> Page[Poll]:
        """Return one page of poll summaries, oldest first."""
        offset = offset_for(number, self._page_size)
        async with self._uow() as repos:
            total = await repos.polls.count(active_only=active_only)
            items = await repos.polls.list_all(
                active_only=active_only, limit=self._page_size, offset=offset
            )
        return Page.create(items, number=number, size=self._page_size, total=total)

    async def detail(self, poll_id: int) -> PollDetail:
        """Return the poll with its schedules and their next five occurrences.

        Raises:
            NotFoundError: no poll has that id.
        """
        now = self._clock.now()
        async with self._uow() as repos:
            poll = await repos.polls.get(poll_id)
            if poll is None:
                raise NotFoundError(f"poll {poll_id} does not exist")
            schedules = await repos.schedules.list_all(poll_id=poll_id)

        return PollDetail(
            poll=poll,
            schedules=tuple(
                ScheduleView(
                    schedule=schedule,
                    next_runs=tuple(
                        next_occurrences(schedule, after=now, count=5)
                        if not schedule.is_paused
                        else ()
                    ),
                )
                for schedule in schedules
            ),
        )

    async def next_run(self, schedule: Schedule) -> datetime | None:
        """Return the next occurrence of a schedule from now, or ``None`` when it has none."""
        return next_occurrence(schedule, after=self._clock.now())
