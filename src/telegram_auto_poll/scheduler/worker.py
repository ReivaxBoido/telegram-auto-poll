"""Bounded delivery worker (FR-126, FR-129, FR-130).

``SEND_CONCURRENCY`` independent loops each claim one row at a time, so the number of in-flight
sends never exceeds the configured bound. A crashed loop is logged and restarted instead of taking
the process down; cancellation stops new claims and lets the current send finish.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from ..services.delivery_service import DeliveryService

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS = 1.0
DEFAULT_ERROR_BACKOFF_SECONDS = 5.0


class DeliveryWorker:
    """Runs the delivery loops until the stop event is set."""

    def __init__(
        self,
        *,
        service: DeliveryService,
        concurrency: int = 4,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        error_backoff_seconds: float = DEFAULT_ERROR_BACKOFF_SECONDS,
    ) -> None:
        self._service = service
        self._concurrency = concurrency
        self._idle_seconds = idle_seconds
        self._error_backoff_seconds = error_backoff_seconds

    async def run(self, stop: asyncio.Event) -> None:
        """Start every loop and return once all of them have stopped."""
        async with asyncio.TaskGroup() as group:
            for index in range(self._concurrency):
                group.create_task(self._loop(index, stop))

    async def _loop(self, index: int, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                outcomes = await self._service.dispatch(limit=1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("delivery worker failed; restarting", extra={"worker": index})
                await self._wait(stop, self._error_backoff_seconds)
                continue
            if not outcomes:
                await self._wait(stop, self._idle_seconds)

    async def _wait(self, stop: asyncio.Event, seconds: float) -> None:
        """Wait for the stop event (or ``seconds``), without busy looping."""
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=seconds)
