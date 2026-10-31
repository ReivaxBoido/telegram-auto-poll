"""Page arithmetic shared by every paginated listing (FR-115, FR-119)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil

#: The bot shows eight rows per page, which fits one Telegram message with inline buttons.
PAGE_SIZE = 8


def offset_for(number: int, size: int = PAGE_SIZE) -> int:
    """Return the SQL offset for a 1-based page number, rejecting impossible arguments."""
    if number < 1:
        raise ValueError("page numbers start at 1")
    if size < 1:
        raise ValueError("page size must be positive")
    return (number - 1) * size


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of a listing plus the counters a keyboard needs."""

    items: tuple[T, ...]
    number: int
    size: int
    total: int

    @classmethod
    def create(
        cls,
        items: Sequence[T],
        *,
        number: int,
        total: int,
        size: int = PAGE_SIZE,
    ) -> Page[T]:
        """Build a page, validating the counters before they reach rendering code."""
        offset_for(number, size)
        if total < 0:
            raise ValueError("total cannot be negative")
        return cls(items=tuple(items), number=number, size=size, total=total)

    @property
    def pages(self) -> int:
        """Total number of pages; an empty listing still has one page."""
        return max(ceil(self.total / self.size), 1)

    @property
    def has_next(self) -> bool:
        return self.number < self.pages

    @property
    def has_previous(self) -> bool:
        return self.number > 1

    @property
    def offset(self) -> int:
        return offset_for(self.number, self.size)
