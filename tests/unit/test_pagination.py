"""Pagination arithmetic (FR-115, FR-119): boundaries the inline keyboards depend on."""

from __future__ import annotations

import pytest

from telegram_auto_poll.services.pagination import PAGE_SIZE, Page, offset_for


def test_page_size_is_eight_rows() -> None:
    assert PAGE_SIZE == 8


@pytest.mark.parametrize(("number", "expected"), [(1, 0), (2, 8), (5, 32)])
def test_offset_for_is_zero_based(number: int, expected: int) -> None:
    assert offset_for(number) == expected


@pytest.mark.parametrize("number", [0, -1])
def test_offset_rejects_page_numbers_below_one(number: int) -> None:
    with pytest.raises(ValueError, match="page numbers start at 1"):
        offset_for(number)


def test_offset_rejects_a_non_positive_size() -> None:
    with pytest.raises(ValueError, match="page size must be positive"):
        offset_for(1, 0)


def test_empty_listing_still_has_one_page() -> None:
    page = Page.create([], number=1, total=0)

    assert page.pages == 1
    assert page.has_next is False
    assert page.has_previous is False
    assert page.items == ()


def test_exact_multiple_of_the_page_size() -> None:
    page = Page.create(list(range(8)), number=1, total=16)

    assert page.pages == 2
    assert page.has_next is True


def test_last_partial_page() -> None:
    page = Page.create(list(range(1)), number=3, total=17)

    assert page.pages == 3
    assert page.has_next is False
    assert page.has_previous is True


def test_page_keeps_items_in_order() -> None:
    page = Page.create(["a", "b"], number=1, total=2)

    assert page.items == ("a", "b")


def test_page_rejects_negative_totals() -> None:
    with pytest.raises(ValueError, match="total cannot be negative"):
        Page.create([], number=1, total=-1)


def test_page_rejects_page_zero() -> None:
    with pytest.raises(ValueError, match="page numbers start at 1"):
        Page.create([], number=0, total=0)


def test_page_offset_matches_the_requested_number() -> None:
    assert Page.create([], number=4, total=100).offset == 24
