"""In-memory doubles for every port.

The doubles are deliberately strict: they reject naive datetimes, record every call, and never
perform I/O, so unit tests can assert behaviour without a network or a real clock (NFR-105).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from telegram_auto_poll.domain.entities import ChatInfo, PollRequest
from telegram_auto_poll.domain.enums import ChatType
from telegram_auto_poll.domain.errors import DeliveryError


class FakeClock:
    """A deterministic ``Clock``. Only aware UTC instants are accepted."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        if self._now.tzinfo is None:
            raise ValueError("FakeClock requires an aware datetime")

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FakeClock requires an aware datetime")
        self._now = moment


@dataclass
class FakeSleeper:
    """Records requested sleeps and returns immediately."""

    waits: list[float] = field(default_factory=list)
    clock: FakeClock | None = None

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        if self.clock is not None:
            self.clock.advance(seconds)


@dataclass
class FakeSender:
    """Records ``PollRequest`` objects and returns incrementing message IDs."""

    requests: list[PollRequest] = field(default_factory=list)
    failures: list[DeliveryError] = field(default_factory=list)
    next_message_id: int = 500

    async def send_poll(self, request: PollRequest) -> int:
        self.requests.append(request)
        if self.failures:
            raise self.failures.pop(0)
        self.next_message_id += 1
        return self.next_message_id

    def fail_next(self, error: DeliveryError) -> None:
        self.failures.append(error)


@dataclass
class FakeChatGateway:
    """A registry of known chats and members."""

    chats: dict[int, ChatInfo] = field(default_factory=dict)
    members: dict[tuple[int, int], bool] = field(default_factory=dict)
    missing: set[int] = field(default_factory=set)
    permission_errors: dict[int, str] = field(default_factory=dict)

    def add_chat(
        self,
        chat_id: int,
        chat_type: ChatType = ChatType.SUPERGROUP,
        title: str = "Test chat",
    ) -> ChatInfo:
        info = ChatInfo(chat_id=chat_id, type=chat_type, title=title)
        self.chats[chat_id] = info
        return info

    async def get_chat(self, chat_id: int) -> ChatInfo:
        if chat_id in self.missing:
            raise LookupError(f"chat {chat_id} not found")
        return self.chats[chat_id]

    async def get_member(self, chat_id: int, user_id: int) -> bool:
        return self.members.get((chat_id, user_id), False)


@dataclass
class FakePacer:
    """Records the chat ids the delivery service paced before sending."""

    chats: list[int] = field(default_factory=list)

    async def acquire(self, chat_id: int) -> None:
        self.chats.append(chat_id)


@dataclass
class FakeNotifier:
    """Records admin alerts."""

    messages: list[str] = field(default_factory=list)

    async def notify_admins(self, text: str) -> None:
        self.messages.append(text)


@dataclass
class FakeBot:
    """Records the PTB calls the adapters make, without a network."""

    messages: list[dict[str, object]] = field(default_factory=list)
    polls: list[dict[str, object]] = field(default_factory=list)
    callback_answers: list[dict[str, object]] = field(default_factory=list)
    commands: list[dict[str, object]] = field(default_factory=list)

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> object:
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": len(self.messages)}

    async def send_poll(
        self,
        chat_id: int,
        question: str,
        options: Iterable[str],
        **kwargs: object,
    ) -> object:
        payload = {
            "chat_id": chat_id,
            "question": question,
            "options": list(options),
            **kwargs,
        }
        self.polls.append(payload)
        return {"message_id": 900 + len(self.polls)}

    async def answer_callback_query(self, callback_query_id: str, **kwargs: object) -> bool:
        self.callback_answers.append({"id": callback_query_id, **kwargs})
        return True

    async def set_my_commands(self, commands: object, **kwargs: object) -> bool:
        self.commands.append({"commands": commands, **kwargs})
        return True
