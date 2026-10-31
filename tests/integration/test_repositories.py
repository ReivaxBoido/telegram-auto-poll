"""Repository behaviour against a real migrated database.

Each case asserts the mapping between rows and domain entities as well as the statement semantics
(ordering, filters, batching). Repository methods never commit; the fixtures supply the transaction.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_auto_poll.db.engine import session_scope
from telegram_auto_poll.db.repositories.audit import AuditRepository
from telegram_auto_poll.db.repositories.chats import ChatRepository
from telegram_auto_poll.db.repositories.deliveries import DeliveryRepository
from telegram_auto_poll.db.repositories.polls import PollRepository
from telegram_auto_poll.db.repositories.schedules import ScheduleRepository
from telegram_auto_poll.domain.entities import Delivery
from telegram_auto_poll.domain.enums import (
    AuditAction,
    ChatType,
    DeliveryStatus,
    ScheduleKind,
    Trigger,
)
from tests.conftest import Seed
from tests.factories import (
    FIXED_NOW,
    make_audit_event,
    make_chat,
    make_delivery,
    make_poll,
    make_schedule,
)

LATER = FIXED_NOW + timedelta(hours=1)

# --------------------------------------------------------------------------------------------
# Chats
# --------------------------------------------------------------------------------------------


async def test_chat_upsert_inserts_and_returns_the_surrogate_id(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        chat = await ChatRepository(session).upsert(make_chat(), now=FIXED_NOW)

    assert chat.id is not None
    assert chat.type is ChatType.SUPERGROUP
    assert chat.is_active is True
    assert chat.created_at == FIXED_NOW


async def test_chat_upsert_updates_metadata_and_reactivates(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        repository = ChatRepository(session)
        created = await repository.upsert(make_chat(), now=FIXED_NOW)
        assert created.id is not None
        await repository.set_active(created.id, active=False, now=FIXED_NOW)
        updated = await repository.upsert(make_chat(title="Renamed", is_active=False), now=LATER)

    assert updated.id == created.id
    assert updated.title == "Renamed"
    assert updated.is_active is False


async def test_chat_lookup_by_telegram_id(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        repository = ChatRepository(session)
        created = await repository.upsert(make_chat(), now=FIXED_NOW)
        found = await repository.get_by_telegram_id(created.telegram_chat_id)
        missing = await repository.get_by_telegram_id(-999)

    assert found == created
    assert missing is None


async def test_chat_get_by_surrogate_id(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        repository = ChatRepository(session)
        created = await repository.upsert(make_chat(), now=FIXED_NOW)
        assert created.id is not None
        assert await repository.get(created.id) == created
        assert await repository.get(created.id + 1000) is None


async def test_chat_list_orders_and_paginates(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        repository = ChatRepository(session)
        for chat_id in (-300, -100, -200):
            await repository.upsert(make_chat(telegram_chat_id=chat_id), now=FIXED_NOW)

    async with session_scope(factory) as session:
        repository = ChatRepository(session)
        everything = await repository.list_all()
        page = await repository.list_all(limit=2, offset=1)

    assert [chat.telegram_chat_id for chat in everything] == [-300, -200, -100]
    assert [chat.telegram_chat_id for chat in page] == [-200, -100]


async def test_chat_list_can_filter_to_active(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        repository = ChatRepository(session)
        first = await repository.upsert(make_chat(telegram_chat_id=-100), now=FIXED_NOW)
        await repository.upsert(make_chat(telegram_chat_id=-200), now=FIXED_NOW)
        assert first.id is not None
        await repository.set_active(first.id, active=False, now=FIXED_NOW)

    async with session_scope(factory) as session:
        repository = ChatRepository(session)
        active = await repository.list_all(active_only=True)
        total = await repository.count()
        active_total = await repository.count(active_only=True)

    assert [chat.telegram_chat_id for chat in active] == [-200]
    assert total == 2
    assert active_total == 1


async def test_chat_set_active_returns_none_for_unknown_ids(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        assert await ChatRepository(session).set_active(404, active=False, now=FIXED_NOW) is None

    async with session_scope(factory) as session:
        assert await ChatRepository(session).count() == 0


# --------------------------------------------------------------------------------------------
# Polls
# --------------------------------------------------------------------------------------------


async def test_poll_add_round_trips_every_field(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        chats = ChatRepository(session)
        first = await chats.upsert(make_chat(telegram_chat_id=-100), now=FIXED_NOW)
        second = await chats.upsert(make_chat(telegram_chat_id=-200), now=FIXED_NOW)
        assert first.id is not None
        assert second.id is not None
        created = await PollRepository(session).add(
            make_poll(
                description="Pick one",
                options=("A", "B"),
                is_anonymous=False,
                allows_multiple_answers=True,
                targets=(second.id, first.id),
            ),
            now=FIXED_NOW,
        )

    assert created.id is not None
    assert created.question == "What should we ship next?"
    assert created.options == ("A", "B")
    assert created.description == "Pick one"
    assert created.is_anonymous is False
    assert created.allows_multiple_answers is True
    assert created.is_archived is False
    assert created.targets == (first.id, second.id)
    assert created.created_at == FIXED_NOW


async def test_poll_add_without_targets(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        created = await PollRepository(session).add(make_poll(targets=()), now=FIXED_NOW)

    assert created.targets == ()
    assert created.description is not None


async def test_poll_get_and_list(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        repository = PollRepository(session)
        first = await repository.add(make_poll(question="First", targets=()), now=FIXED_NOW)
        await repository.add(make_poll(question="Second", targets=()), now=FIXED_NOW)
        assert first.id is not None

        assert await repository.get(first.id) == first
        assert await repository.get(first.id + 500) is None
        listed = await repository.list_all()
        page = await repository.list_all(limit=1, offset=1)

    assert [poll.question for poll in listed] == ["First", "Second"]
    assert [poll.question for poll in page] == ["Second"]


async def test_poll_list_can_filter_archived(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        repository = PollRepository(session)
        kept = await repository.add(make_poll(question="Kept", targets=()), now=FIXED_NOW)
        archived = await repository.add(make_poll(question="Gone", targets=()), now=FIXED_NOW)
        assert archived.id is not None
        await repository.set_active(archived.id, active=False, now=FIXED_NOW)

    async with session_scope(factory) as session:
        repository = PollRepository(session)
        active = await repository.list_all(active_only=True)
        assert [poll.id for poll in active] == [kept.id]
        assert await repository.count() == 2
        assert await repository.count(active_only=True) == 1


async def test_poll_set_targets_replaces_the_association(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        chats = ChatRepository(session)
        first = await chats.upsert(make_chat(telegram_chat_id=-100), now=FIXED_NOW)
        second = await chats.upsert(make_chat(telegram_chat_id=-200), now=FIXED_NOW)
        assert first.id is not None
        assert second.id is not None
        polls = PollRepository(session)
        created = await polls.add(make_poll(targets=(first.id,)), now=FIXED_NOW)
        assert created.id is not None

        await polls.set_targets(created.id, [second.id, second.id], now=LATER)
        reread = await polls.get(created.id)

    assert reread is not None
    assert reread.targets == (second.id,)


async def test_poll_set_active_returns_none_for_unknown_ids(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        assert await PollRepository(session).set_active(404, active=True, now=FIXED_NOW) is None


# --------------------------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------------------------


async def test_schedule_add_round_trips_daily(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        stored = await ScheduleRepository(session).get(seed.schedule_id)

    assert stored is not None
    assert stored.kind is ScheduleKind.DAILY
    assert stored.time_of_day == time(9, 0)
    assert stored.timezone == "UTC"
    assert stored.weekdays == ()
    assert stored.next_run_at == datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    assert stored.is_paused is False
    assert stored.created_at == FIXED_NOW


async def test_schedule_add_round_trips_weekly_and_monthly(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = ScheduleRepository(session)
        weekly = await repository.add(
            make_schedule(
                poll_id=seed.poll_id,
                kind=ScheduleKind.WEEKLY,
                weekdays=(1, 5),
                time_of_day=time(18, 30),
                start_date=date(2026, 3, 1),
                end_date=date(2026, 12, 31),
            ),
            now=FIXED_NOW,
        )
        monthly = await repository.add(
            make_schedule(poll_id=seed.poll_id, kind=ScheduleKind.MONTHLY, day_of_month=15),
            now=FIXED_NOW,
        )

    assert weekly.weekdays == (1, 5)
    assert weekly.time_of_day == time(18, 30)
    assert weekly.start_date == date(2026, 3, 1)
    assert weekly.end_date == date(2026, 12, 31)
    assert monthly.day_of_month == 15


async def test_schedule_add_round_trips_a_one_off(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    run_at = FIXED_NOW + timedelta(days=2)
    async with session_scope(factory) as session:
        stored = await ScheduleRepository(session).add(
            make_schedule(
                poll_id=seed.poll_id,
                kind=ScheduleKind.ONCE,
                time_of_day=None,
                run_at=run_at,
                next_run_at=run_at,
            ),
            now=FIXED_NOW,
        )

    assert stored.kind is ScheduleKind.ONCE
    assert stored.run_at == run_at
    assert stored.time_of_day is None
    assert stored.weekdays == ()


async def test_schedule_list_filters(seed: Seed, factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        repository = ScheduleRepository(session)
        other_poll = await PollRepository(session).add(make_poll(targets=()), now=FIXED_NOW)
        assert other_poll.id is not None
        second = await repository.add(
            make_schedule(poll_id=seed.poll_id, kind=ScheduleKind.MONTHLY, day_of_month=3),
            now=FIXED_NOW,
        )
        assert second.id is not None
        await repository.set_paused(second.id, paused=True, next_run_at=None, now=FIXED_NOW)

    async with session_scope(factory) as session:
        repository = ScheduleRepository(session)
        assert len(await repository.list_all()) == 2
        assert len(await repository.list_all(poll_id=seed.poll_id)) == 2
        active = await repository.list_all(active_only=True)
        page = await repository.list_all(limit=1, offset=1)
        assert await repository.count_active() == 1

    assert [schedule.id for schedule in active] == [seed.schedule_id]
    assert [schedule.id for schedule in page] == [second.id]


async def test_schedule_due_before_orders_and_excludes_ineligible_rows(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = ScheduleRepository(session)
        paused = await repository.add(
            make_schedule(poll_id=seed.poll_id, kind=ScheduleKind.MONTHLY, day_of_month=3),
            now=FIXED_NOW,
        )
        assert paused.id is not None
        await repository.set_paused(paused.id, paused=True, next_run_at=None, now=FIXED_NOW)
        future = await repository.add(
            make_schedule(
                poll_id=seed.poll_id,
                kind=ScheduleKind.MONTHLY,
                day_of_month=4,
                next_run_at=FIXED_NOW + timedelta(days=30),
            ),
            now=FIXED_NOW,
        )
        assert future.id is not None

    async with session_scope(factory) as session:
        due = await ScheduleRepository(session).due_before(FIXED_NOW, limit=10)

    assert [schedule.id for schedule in due] == [seed.schedule_id]


async def test_schedule_update_next_run_advances_the_handoff(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = ScheduleRepository(session)
        await repository.update_next_run(
            seed.schedule_id,
            next_run_at=FIXED_NOW + timedelta(days=1),
            last_run_at=FIXED_NOW,
            now=FIXED_NOW,
        )
        stored = await repository.get(seed.schedule_id)
        await repository.update_next_run(404, next_run_at=None, last_run_at=None, now=FIXED_NOW)

    assert stored is not None
    assert stored.next_run_at == FIXED_NOW + timedelta(days=1)
    assert stored.last_run_at == FIXED_NOW


async def test_schedule_pause_and_delete(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = ScheduleRepository(session)
        paused = await repository.set_paused(
            seed.schedule_id, paused=True, next_run_at=None, now=FIXED_NOW
        )
        assert (
            await repository.set_paused(404, paused=True, next_run_at=None, now=FIXED_NOW) is None
        )

    assert paused is not None
    assert paused.is_paused is True
    assert paused.next_run_at is None

    async with session_scope(factory) as session:
        repository = ScheduleRepository(session)
        assert await repository.delete(seed.schedule_id) is True
        assert await repository.delete(seed.schedule_id) is False
        assert await repository.get(seed.schedule_id) is None


# --------------------------------------------------------------------------------------------
# Deliveries
# --------------------------------------------------------------------------------------------


async def test_insert_many_ignore_with_no_rows_is_a_no_op(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        assert await DeliveryRepository(session).insert_many_ignore([], now=FIXED_NOW) == 0


async def test_insert_many_ignore_suppresses_duplicate_scheduled_rows(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    row = make_delivery(poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id)

    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        assert await repository.insert_many_ignore([row, row], now=FIXED_NOW) == 1

    async with session_scope(factory) as session:
        assert await DeliveryRepository(session).counts() == {
            DeliveryStatus.PENDING: 1,
            DeliveryStatus.SENDING: 0,
            DeliveryStatus.SENT: 0,
            DeliveryStatus.FAILED: 0,
            DeliveryStatus.SKIPPED: 0,
        }


async def test_insert_many_ignore_allows_repeated_manual_rows(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    manual = Delivery(
        poll_id=seed.poll_id,
        chat_id=seed.chat_id,
        trigger=Trigger.MANUAL,
        status=DeliveryStatus.PENDING,
        scheduled_for=FIXED_NOW,
    )

    async with session_scope(factory) as session:
        assert (
            await DeliveryRepository(session).insert_many_ignore([manual, manual], now=FIXED_NOW)
            == 2
        )


async def test_insert_many_ignore_rejects_a_row_without_an_occurrence(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    row = make_delivery(poll_id=seed.poll_id, chat_id=seed.chat_id, scheduled_for=None)

    async with session_scope(factory) as session:
        with pytest.raises(ValueError, match="scheduled_for"):
            await DeliveryRepository(session).insert_many_ignore([row], now=FIXED_NOW)


async def test_delivery_get_and_list_for_occurrence(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    other_chat = seed.chat_id + 1
    rows = [
        make_delivery(
            poll_id=seed.poll_id, chat_id=other_chat, schedule_id=seed.schedule_id, id=None
        ),
        make_delivery(
            poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id, id=None
        ),
    ]

    async with session_scope(factory) as session:
        # The second chat needs to exist before the delivery can reference it.
        await ChatRepository(session).upsert(
            make_chat(id=None, telegram_chat_id=-1_001_234_567_891), now=FIXED_NOW
        )
        repository = DeliveryRepository(session)
        await repository.insert_many_ignore(rows, now=FIXED_NOW)

    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        stored = await repository.list_for_occurrence(
            seed.schedule_id, datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
        )
        assert [delivery.chat_id for delivery in stored] == sorted([seed.chat_id, other_chat])

        first = stored[0]
        assert first.id is not None
        assert await repository.get(first.id) == first
        assert await repository.get(first.id + 10_000) is None


async def test_claim_due_increments_attempts_and_marks_sending(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        await DeliveryRepository(session).insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=seed.schedule_id,
                    next_attempt_at=FIXED_NOW - timedelta(minutes=1),
                )
            ],
            now=FIXED_NOW,
        )

    async with session_scope(factory) as session:
        claimed = await DeliveryRepository(session).claim_due(FIXED_NOW, limit=10)

    assert len(claimed) == 1
    assert claimed[0].status is DeliveryStatus.SENDING
    assert claimed[0].attempt_count == 1
    assert claimed[0].started_at == FIXED_NOW

    async with session_scope(factory) as session:
        assert await DeliveryRepository(session).claim_due(FIXED_NOW, limit=10) == []


async def test_claim_due_skips_future_and_terminal_rows(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        await repository.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=seed.schedule_id,
                    next_attempt_at=FIXED_NOW + timedelta(minutes=5),
                )
            ],
            now=FIXED_NOW,
        )
        manual = Delivery(
            poll_id=seed.poll_id,
            chat_id=seed.chat_id,
            trigger=Trigger.MANUAL,
            status=DeliveryStatus.PENDING,
            scheduled_for=FIXED_NOW,
            next_attempt_at=None,
        )
        await repository.insert_many_ignore([manual], now=FIXED_NOW)

    async with session_scope(factory) as session:
        claimed = await DeliveryRepository(session).claim_due(FIXED_NOW, limit=10)

    assert [delivery.trigger for delivery in claimed] == [Trigger.MANUAL]


async def test_claim_due_respects_the_batch_limit(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        for offset in range(3):
            await repository.insert_many_ignore(
                [
                    Delivery(
                        poll_id=seed.poll_id,
                        chat_id=seed.chat_id,
                        trigger=Trigger.MANUAL,
                        status=DeliveryStatus.PENDING,
                        scheduled_for=FIXED_NOW + timedelta(seconds=offset),
                        next_attempt_at=None,
                    )
                ],
                now=FIXED_NOW,
            )

    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        first = await repository.claim_due(FIXED_NOW, limit=2)
        second = await repository.claim_due(FIXED_NOW, limit=2)

    assert len(first) == 2
    assert len(second) == 1


async def test_mark_sent_records_the_message_and_completion(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        await repository.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id
                )
            ],
            now=FIXED_NOW,
        )
        claimed = await repository.claim_due(FIXED_NOW, limit=1)
        delivery_id = claimed[0].id
        assert delivery_id is not None
        sent = await repository.mark_sent(delivery_id, telegram_message_id=4242, now=FIXED_NOW)
        assert await repository.mark_sent(404, telegram_message_id=1, now=FIXED_NOW) is None

    assert sent is not None
    assert sent.status is DeliveryStatus.SENT
    assert sent.telegram_message_id == 4242
    assert sent.completed_at == FIXED_NOW
    assert sent.next_attempt_at is None


async def test_mark_retry_returns_the_row_to_pending(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        await repository.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id
                )
            ],
            now=FIXED_NOW,
        )
        claimed = await repository.claim_due(FIXED_NOW, limit=1)
        delivery_id = claimed[0].id
        assert delivery_id is not None
        retried = await repository.mark_retry(
            delivery_id, next_attempt_at=LATER, error="timeout", now=FIXED_NOW
        )
        assert (
            await repository.mark_retry(404, next_attempt_at=LATER, error="x", now=FIXED_NOW)
            is None
        )

    assert retried is not None
    assert retried.status is DeliveryStatus.PENDING
    assert retried.next_attempt_at == LATER
    assert retried.last_error == "timeout"


async def test_mark_failed_and_skipped_are_terminal(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        await repository.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=seed.schedule_id,
                    scheduled_for=FIXED_NOW,
                ),
                make_delivery(
                    poll_id=seed.poll_id,
                    chat_id=seed.chat_id,
                    schedule_id=seed.schedule_id,
                    scheduled_for=FIXED_NOW + timedelta(hours=1),
                ),
            ],
            now=FIXED_NOW,
        )
        claimed = await repository.claim_due(FIXED_NOW, limit=2)
        first_id, second_id = (delivery.id for delivery in claimed)

        assert first_id is not None
        assert second_id is not None
        failed = await repository.mark_failed(first_id, error="chat not found", now=FIXED_NOW)
        skipped = await repository.mark_skipped(
            second_id, reason="missed_grace_window", now=FIXED_NOW
        )
        assert await repository.mark_failed(404, error="x", now=FIXED_NOW) is None
        assert await repository.mark_skipped(404, reason="x", now=FIXED_NOW) is None

    assert failed is not None
    assert failed.status is DeliveryStatus.FAILED
    assert failed.completed_at == FIXED_NOW
    assert skipped is not None
    assert skipped.status is DeliveryStatus.SKIPPED
    assert skipped.skip_reason == "missed_grace_window"
    assert skipped.completed_at == FIXED_NOW


async def test_fail_stale_sending_recovers_a_crashed_attempt(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        await repository.insert_many_ignore(
            [
                make_delivery(
                    poll_id=seed.poll_id, chat_id=seed.chat_id, schedule_id=seed.schedule_id
                )
            ],
            now=FIXED_NOW,
        )
        await repository.claim_due(FIXED_NOW, limit=1)

    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        assert await repository.fail_stale_sending(reason="interrupted", now=LATER) == 1
        assert await repository.fail_stale_sending(reason="interrupted", now=LATER) == 0
        failures = await repository.list_recent_failures()

    assert len(failures) == 1
    assert failures[0].status is DeliveryStatus.FAILED
    assert failures[0].last_error == "interrupted"
    assert failures[0].completed_at == LATER


async def test_list_recent_failures_orders_and_limits(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        for offset, completed in enumerate((LATER, FIXED_NOW)):
            await repository.insert_many_ignore(
                [
                    make_delivery(
                        poll_id=seed.poll_id,
                        chat_id=seed.chat_id,
                        schedule_id=seed.schedule_id,
                        scheduled_for=FIXED_NOW + timedelta(days=offset + 1),
                        status=DeliveryStatus.FAILED,
                        completed_at=completed,
                    )
                ],
                now=FIXED_NOW,
            )

    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        recent = await repository.list_recent_failures(limit=2)
        limited = await repository.list_recent_failures(limit=1)

    assert [delivery.completed_at for delivery in recent] == [LATER, FIXED_NOW]
    assert len(limited) == 1


async def test_delete_batch_before_is_batched(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        for day in (30, 29, 28, 1):
            await repository.insert_many_ignore(
                [
                    make_delivery(
                        poll_id=seed.poll_id,
                        chat_id=seed.chat_id,
                        schedule_id=seed.schedule_id,
                        scheduled_for=FIXED_NOW - timedelta(days=day),
                    )
                ],
                now=FIXED_NOW,
            )

    cutoff = FIXED_NOW - timedelta(days=10)
    async with session_scope(factory) as session:
        repository = DeliveryRepository(session)
        assert await repository.delete_batch_before(cutoff, limit=2) == 2
        assert await repository.delete_batch_before(cutoff, limit=2) == 1
        assert await repository.delete_batch_before(cutoff, limit=2) == 0

    async with session_scope(factory) as session:
        assert sum((await DeliveryRepository(session).counts()).values()) == 1


# --------------------------------------------------------------------------------------------
# Audit events
# --------------------------------------------------------------------------------------------


async def test_audit_add_round_trips_details(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        created = await AuditRepository(session).add(
            make_audit_event(details={"question": "Ship it?", "options": ["a", "b"]}),
            now=FIXED_NOW,
        )

    assert created.id is not None
    assert created.action is AuditAction.POLL_CREATE
    assert created.entity_type == "poll"
    assert created.entity_id == 10
    assert created.details == {"question": "Ship it?", "options": ["a", "b"]}
    assert created.created_at == FIXED_NOW


async def test_audit_add_accepts_no_details_and_defaults_the_timestamp(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        created = await AuditRepository(session).add(
            make_audit_event(details=None, created_at=None), now=FIXED_NOW
        )

    assert created.details is None
    assert created.created_at == FIXED_NOW


async def test_audit_list_recent_is_newest_first(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        repository = AuditRepository(session)
        for action in (AuditAction.CHAT_REGISTER, AuditAction.POLL_CREATE):
            await repository.add(make_audit_event(action=action), now=FIXED_NOW)

    async with session_scope(factory) as session:
        repository = AuditRepository(session)
        recent = await repository.list_recent()
        limited = await repository.list_recent(limit=1)

    assert [event.action for event in recent] == [
        AuditAction.POLL_CREATE,
        AuditAction.CHAT_REGISTER,
    ]
    assert len(limited) == 1


async def test_audit_history_for_one_entity(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        repository = AuditRepository(session)
        await repository.add(make_audit_event(entity_id=10), now=FIXED_NOW)
        await repository.add(make_audit_event(entity_type="schedule", entity_id=100), now=FIXED_NOW)
        await repository.add(make_audit_event(entity_id=11), now=FIXED_NOW)

    async with session_scope(factory) as session:
        history = await AuditRepository(session).list_for_entity("poll", 10)

    assert len(history) == 1
    assert history[0].entity_id == 10


async def test_audit_delete_batch_before_is_batched(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        repository = AuditRepository(session)
        for days in (120, 100, 1):
            await repository.add(
                make_audit_event(created_at=FIXED_NOW - timedelta(days=days)), now=FIXED_NOW
            )

    cutoff = FIXED_NOW - timedelta(days=90)
    async with session_scope(factory) as session:
        repository = AuditRepository(session)
        assert await repository.delete_batch_before(cutoff, limit=1) == 1
        assert await repository.delete_batch_before(cutoff, limit=10) == 1
        assert await repository.delete_batch_before(cutoff, limit=10) == 0
        remaining = await repository.list_recent()

    assert len(remaining) == 1


def test_audit_details_json_encoding_is_deterministic() -> None:
    from telegram_auto_poll.db.repositories.common import dumps

    assert dumps({"b": 1, "a": 2}) == json.dumps(
        {"b": 1, "a": 2}, separators=(",", ":"), sort_keys=True
    )


async def test_poll_list_all_on_an_empty_database(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        repository = PollRepository(session)
        assert await repository.list_all() == []
        assert await repository.count() == 0


async def test_schedule_update_next_run_without_a_last_run(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = ScheduleRepository(session)
        await repository.update_next_run(
            seed.schedule_id, next_run_at=None, last_run_at=None, now=FIXED_NOW
        )
        stored = await repository.get(seed.schedule_id)

    assert stored is not None
    assert stored.next_run_at is None
    assert stored.last_run_at is None


async def test_schedule_active_runs_returns_upcoming_pairs(
    seed: Seed, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(factory) as session:
        repository = ScheduleRepository(session)
        paused = await repository.add(
            make_schedule(poll_id=seed.poll_id, kind=ScheduleKind.MONTHLY, day_of_month=3),
            now=FIXED_NOW,
        )
        assert paused.id is not None
        await repository.set_paused(paused.id, paused=True, next_run_at=None, now=FIXED_NOW)
        expired = await repository.add(
            make_schedule(
                poll_id=seed.poll_id,
                kind=ScheduleKind.MONTHLY,
                day_of_month=4,
                next_run_at=None,
            ),
            now=FIXED_NOW,
        )
        assert expired.id is not None

        runs = await repository.active_runs()

    assert runs == [(seed.schedule_id, datetime(2026, 3, 1, 9, 0, tzinfo=UTC))]
