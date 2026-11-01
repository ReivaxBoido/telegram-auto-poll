"""Callback codec (SPEC-003 FR-317)."""

from __future__ import annotations

import pytest

from telegram_auto_poll.domain.callbacks import (
    MAX_CALLBACK_BYTES,
    CallbackDataError,
    confirm,
    decode,
    encode,
    poll_action,
    target_cancel,
    target_done,
    target_page,
    target_toggle,
    vote,
    wizard_action,
)


def test_every_generated_callback_round_trips_and_fits() -> None:
    generated = [
        vote(12, 3),
        poll_action("close", 12),
        target_toggle(12, -1_001_234_567_890),
        target_page(12, 2),
        target_done(12),
        target_cancel(12),
        wizard_action("option_remove", 0),
        confirm("delete", 12),
    ]
    for data in generated:
        assert len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES
        assert decode(data).namespace in {"v", "p", "t", "w", "c"}


def test_decode_returns_the_parts() -> None:
    callback = decode(vote(12, 3))
    assert callback.namespace == "v"
    assert callback.action == "toggle"
    assert callback.argument == "12_3"


def test_encode_rejects_unknown_namespace_action_and_argument() -> None:
    with pytest.raises(CallbackDataError, match="unknown callback namespace"):
        encode("z", "toggle", 1)
    with pytest.raises(CallbackDataError, match="invalid callback action"):
        encode("v", "Bad Action", 1)
    with pytest.raises(CallbackDataError, match="invalid callback argument"):
        encode("v", "toggle", "bad:value")


def test_encode_rejects_payloads_over_64_bytes() -> None:
    with pytest.raises(CallbackDataError, match="exceeds 64 bytes"):
        encode("v", "a" * 31, "b" * 40)


def test_decode_rejects_short_wrong_version_and_unknown_namespace() -> None:
    with pytest.raises(CallbackDataError, match="too short"):
        decode("v1:v")
    with pytest.raises(CallbackDataError, match="unsupported callback version"):
        decode("v2:v:toggle:1")
    with pytest.raises(CallbackDataError, match="unknown callback namespace"):
        decode("v1:z:toggle:1")
