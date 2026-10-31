"""Ports: the only seams the services depend on (constitution §13.4).

Each protocol is implemented by an adapter in ``bot/``, ``db/``, or ``clock.py``; services never
import a concrete adapter, which is what lets every service test run without Telegram or SQLite.

Bodies raise ``NotImplementedError`` rather than using ``...`` so that the sanctioned constitution
§11.3 exclusion applies and the protocols do not distort coverage numbers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .entities import ChatInfo, PollRequest


class Clock(Protocol):
    """Supplies the current instant as an aware UTC datetime."""

    def now(self) -> datetime:
        raise NotImplementedError


class Sleeper(Protocol):
    """Suspends the caller; tests record the request instead of waiting."""

    async def sleep(self, seconds: float) -> None:
        raise NotImplementedError


class PollSender(Protocol):
    """Sends one poll and returns the Telegram message id."""

    async def send_poll(self, request: PollRequest) -> int:
        raise NotImplementedError


class ChatGateway(Protocol):
    """Reads chat metadata and bot membership from Telegram."""

    async def get_chat(self, chat_id: int) -> ChatInfo:
        raise NotImplementedError

    async def get_member(self, chat_id: int, user_id: int) -> bool:
        raise NotImplementedError


class Pacer(Protocol):
    """Waits until the Telegram rate budget allows a send to ``chat_id``."""

    async def acquire(self, chat_id: int) -> None:
        raise NotImplementedError


class AdminNotifier(Protocol):
    """Delivers an operational alert to every configured admin."""

    async def notify_admins(self, text: str) -> None:
        raise NotImplementedError
