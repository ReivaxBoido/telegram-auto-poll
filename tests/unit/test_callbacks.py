"""Callback codec (T-040): round trips, version rejection, and the 64-byte ceiling."""

from __future__ import annotations

import pytest

from telegram_auto_poll.bot.callbacks import (
    CALLBACK_VERSION,
    MAX_CALLBACK_BYTES,
    Callback,
    CallbackDataError,
    decode,
    encode,
    poll_action,
    schedule_action,
    wizard_action,
)

#: Every callback the application can generate, with a representative argument.
SAMPLE_CALLBACKS = [
    ("p", "view", 12),
    ("p", "list", 2),
    ("p", "sendnow", 12),
    ("s", "view", 34),
    ("s", "list", 1),
    ("s", "pause", 34),
    ("s", "resume", 34),
    ("s", "delete", 34),
    ("w", "start", None),
    ("w", "cancel", None),
    ("w", "step", "options"),
    ("w", "option_done", None),
    ("w", "anonymous_toggle", None),
    ("w", "multiple_toggle", None),
    ("w", "target_toggle", "-1001234567890"),
    ("w", "tgt", -1001234567890),
    ("w", "tgtp", 2),
    ("w", "wd", 7),
    ("w", "opt", 11),
    ("w", "kind", "daily"),
    ("w", "kind", "none"),
    ("w", "add", None),
    ("w", "anon", None),
    ("w", "multi", None),
    ("w", "back", None),
    ("w", "confirm", None),
]


def test_version_is_v1() -> None:
    assert CALLBACK_VERSION == "v1"
    assert MAX_CALLBACK_BYTES == 64


@pytest.mark.parametrize(("namespace", "action", "argument"), SAMPLE_CALLBACKS)
def test_every_generated_callback_round_trips_and_fits(
    namespace: str, action: str, argument: object
) -> None:
    data = encode(namespace, action, argument)  # type: ignore[arg-type]

    assert len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES
    assert decode(data) == Callback(
        namespace=namespace, action=action, argument=None if argument is None else str(argument)
    )


def test_named_builders_round_trip() -> None:
    assert decode(poll_action("view", 12)) == Callback("p", "view", "12")
    assert decode(schedule_action("pause", 34)) == Callback("s", "pause", "34")
    assert decode(wizard_action("target_toggle", -100)) == Callback("w", "target_toggle", "-100")


def test_encoding_rejects_an_unknown_namespace() -> None:
    with pytest.raises(CallbackDataError, match="namespace"):
        encode("x", "view", 1)


def test_encoding_rejects_an_invalid_action() -> None:
    with pytest.raises(CallbackDataError, match="action"):
        encode("p", "View:1")


def test_encoding_rejects_an_invalid_argument() -> None:
    with pytest.raises(CallbackDataError, match="argument"):
        encode("p", "view", "1:2")


def test_encoding_rejects_an_oversized_payload() -> None:
    action = "a" * 31
    argument = "9" * 40

    with pytest.raises(CallbackDataError, match="64 bytes"):
        encode("p", action, argument)


def test_decoding_rejects_a_short_payload() -> None:
    with pytest.raises(CallbackDataError, match="too short"):
        decode("v1:p")


def test_decoding_rejects_a_future_version() -> None:
    with pytest.raises(CallbackDataError, match="version"):
        decode("v2:p:view:1")


def test_decoding_rejects_an_unknown_namespace() -> None:
    with pytest.raises(CallbackDataError, match="namespace"):
        decode("v1:x:view:1")


def test_decoding_rejects_an_invalid_action() -> None:
    with pytest.raises(CallbackDataError, match="action"):
        decode("v1:p:View:1")


def test_decoding_rejects_an_invalid_argument() -> None:
    with pytest.raises(CallbackDataError, match="argument"):
        decode("v1:p:view:1:2")


def test_decoding_accepts_an_argument_with_underscores_and_dashes() -> None:
    assert decode("v1:w:target_toggle:-1001234567890").argument == "-1001234567890"


def test_wizard_builders_cover_every_action() -> None:
    from telegram_auto_poll.bot import callbacks

    for action, argument in (
        (callbacks.WIZARD_BACK, None),
        (callbacks.WIZARD_NEXT, None),
        (callbacks.WIZARD_DONE, None),
        (callbacks.WIZARD_SKIP, None),
        (callbacks.WIZARD_CANCEL, None),
        (callbacks.WIZARD_ANONYMITY, None),
        (callbacks.WIZARD_MULTIPLE, None),
        (callbacks.WIZARD_ADD_OPTION, None),
        (callbacks.WIZARD_KIND, "weekly"),
        (callbacks.WIZARD_WEEKDAY, 4),
        (callbacks.WIZARD_TARGET, -1001234567890),
        (callbacks.WIZARD_TARGET_PAGE, 3),
        (callbacks.WIZARD_OPTION_REMOVE, 2),
    ):
        data = callbacks.wizard_action(action, argument)
        decoded = decode(data)
        assert decoded.namespace == "w"
        assert decoded.action == action
        assert decoded.argument == (None if argument is None else str(argument))
        assert len(data.encode()) <= 64
