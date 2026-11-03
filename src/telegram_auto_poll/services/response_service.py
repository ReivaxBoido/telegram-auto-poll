"""Participant vote toggling (SPEC-003 FR-318 - FR-325).

A vote is valid only when the callback points at a stored, sent message of an open instance and the
option belongs to that instance's poll. The actor is always the callback sender; the service never
trusts callback data as identity. A successful toggle marks the instance dirty for the broadcaster.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db.uow import Repositories, UnitOfWork
from ..domain.entities import Poll, PollInstance
from ..domain.enums import MessageStatus, PollStatus, VoteAction
from ..domain.errors import PollClosedError, ResponseRejectedError
from ..domain.ports import Clock
from ..domain.validation import validate_first_name
from .broadcaster import ResponseBroadcaster

MESSAGE_GONE = "This poll message is no longer active."
POLL_GONE = "This poll is no longer available."
OPTION_GONE = "That option no longer exists."
VOTING_CLOSED = "Voting is closed."


@dataclass(frozen=True, slots=True)
class VoteResult:
    """What a successful toggle changed."""

    action: VoteAction
    option_label: str
    instance_id: int
    option_id: int


class ResponseService:
    """Toggles a participant's response and queues a render pass."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        clock: Clock,
        broadcaster: ResponseBroadcaster,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._broadcaster = broadcaster

    async def toggle(
        self,
        *,
        instance_id: int,
        option_id: int,
        telegram_user_id: int,
        first_name: str | None,
        chat_id: int | None = None,
        message_id: int | None = None,
        inline_message_id: str | None = None,
    ) -> VoteResult:
        """Add or remove one response.

        Raises:
            PollClosedError: the instance is closed or deleted.
            ResponseRejectedError: the poll, option, or message is not valid for this vote.
        """
        now = self._clock.now()
        clean_name = validate_first_name(first_name)
        async with self._uow() as repos:
            instance, poll = await self._load_open_poll(repos, instance_id)
            option = poll.option(option_id)
            if option is None:
                raise ResponseRejectedError(OPTION_GONE)
            await self._require_message(
                repos,
                instance_id=instance.id or instance_id,
                chat_id=chat_id,
                message_id=message_id,
                inline_message_id=inline_message_id,
            )
            added = await repos.responses.toggle(
                instance_id=instance.id or instance_id,
                option_id=option_id,
                telegram_user_id=telegram_user_id,
                first_name=clean_name,
                now=now,
            )

        self._broadcaster.mark_dirty(instance_id)
        return VoteResult(
            action=VoteAction.ADDED if added else VoteAction.REMOVED,
            option_label=option.label,
            instance_id=instance_id,
            option_id=option_id,
        )

    async def _load_open_poll(
        self, repos: Repositories, instance_id: int
    ) -> tuple[PollInstance, Poll]:
        instance = await repos.instances.get(instance_id)
        if instance is None:
            raise ResponseRejectedError(POLL_GONE)
        poll = await repos.polls.get(instance.poll_id)
        if poll is None or poll.status is PollStatus.DELETED:
            raise ResponseRejectedError(POLL_GONE)
        if not instance.is_open:
            raise PollClosedError(VOTING_CLOSED)
        return instance, poll

    async def _require_message(
        self,
        repos: Repositories,
        *,
        instance_id: int,
        chat_id: int | None,
        message_id: int | None,
        inline_message_id: str | None,
    ) -> None:
        if inline_message_id is not None:
            message = await repos.messages.get_for_inline(
                instance_id=instance_id,
                inline_message_id=inline_message_id,
            )
        elif chat_id is not None and message_id is not None:
            chat = await repos.chats.get_by_telegram_id(chat_id)
            if chat is None or chat.id is None:
                raise ResponseRejectedError(MESSAGE_GONE)
            message = await repos.messages.get_for_chat_message(
                instance_id=instance_id,
                chat_id=chat.id,
                telegram_message_id=message_id,
            )
        else:
            raise ResponseRejectedError(MESSAGE_GONE)

        if message is None or message.status is not MessageStatus.SENT:
            raise ResponseRejectedError(MESSAGE_GONE)
