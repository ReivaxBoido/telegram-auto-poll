"""In-memory doubles for every port.

The doubles are deliberately strict: they reject naive datetimes, record every call, and never
perform I/O, so unit tests can assert behaviour without a network or a real clock (NFR-304).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from telegram_auto_poll.domain.entities import ChatInfo, Keyboard
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
class FakeMessageGateway:
    """Records sends and edits, and can fail on demand."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)
    send_failures: list[DeliveryError] = field(default_factory=list)
    edit_failures: list[DeliveryError] = field(default_factory=list)
    next_message_id: int = 500

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Keyboard | None = None,
    ) -> int:
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})
        if self.send_failures:
            raise self.send_failures.pop(0)
        self.next_message_id += 1
        return self.next_message_id

    async def edit_message(
        self,
        *,
        text: str,
        chat_id: int | None = None,
        message_id: int | None = None,
        inline_message_id: str | None = None,
        keyboard: Keyboard | None = None,
    ) -> None:
        self.edits.append(
            {
                "text": text,
                "chat_id": chat_id,
                "message_id": message_id,
                "inline_message_id": inline_message_id,
                "keyboard": keyboard,
            }
        )
        if self.edit_failures:
            raise self.edit_failures.pop(0)


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
    """Records the chat ids paced before sending."""

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
class FakeObserver:
    """Records posting outcomes for the worker tests."""

    sent_count: int = 0
    failed_reasons: list[str] = field(default_factory=list)
    skipped_count: int = 0

    def sent(self) -> None:
        self.sent_count += 1

    def failed(self, reason: str) -> None:
        self.failed_reasons.append(reason)

    def skipped(self) -> None:
        self.skipped_count += 1


@dataclass
class FakeBot:
    """Records the PTB calls the adapters make, without a network."""

    messages: list[dict[str, object]] = field(default_factory=list)
    edits: list[dict[str, object]] = field(default_factory=list)
    callback_answers: list[dict[str, object]] = field(default_factory=list)
    commands: list[dict[str, object]] = field(default_factory=list)

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> object:
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": len(self.messages)}

    async def edit_message_text(self, **kwargs: object) -> object:
        self.edits.append(kwargs)
        return True

    async def answer_callback_query(self, callback_query_id: str, **kwargs: object) -> bool:
        self.callback_answers.append({"id": callback_query_id, **kwargs})
        return True

    async def set_my_commands(self, commands: object, **kwargs: object) -> bool:
        self.commands.append({"commands": commands, **kwargs})
        return True
