"""Versioned callback data (spec §8.3).

Envelope: ``v1:<namespace>:<action>[:<argument>]``, at most 64 bytes. Telegram truncates longer
payloads silently, so encoding refuses them instead of producing an unclickable button.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CALLBACK_VERSION = "v1"
MAX_CALLBACK_BYTES = 64

NAMESPACE_POLL = "p"
NAMESPACE_SCHEDULE = "s"
NAMESPACE_WIZARD = "w"

NAMESPACES = frozenset({NAMESPACE_POLL, NAMESPACE_SCHEDULE, NAMESPACE_WIZARD})

_ACTION = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_ARGUMENT = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class CallbackDataError(ValueError):
    """Callback data is malformed, unknown, or too long."""


@dataclass(frozen=True, slots=True)
class Callback:
    """A decoded callback."""

    namespace: str
    action: str
    argument: str | None = None


def encode(namespace: str, action: str, argument: str | int | None = None) -> str:
    """Build callback data, rejecting anything Telegram would truncate or the bot would not parse.

    Raises:
        CallbackDataError: unknown namespace, invalid action/argument, or more than 64 bytes.
    """
    if namespace not in NAMESPACES:
        raise CallbackDataError(f"unknown callback namespace {namespace!r}")
    if not _ACTION.fullmatch(action):
        raise CallbackDataError(f"invalid callback action {action!r}")
    parts = [CALLBACK_VERSION, namespace, action]
    if argument is not None:
        text = str(argument)
        if not _ARGUMENT.fullmatch(text):
            raise CallbackDataError(f"invalid callback argument {text!r}")
        parts.append(text)
    data = ":".join(parts)
    if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise CallbackDataError(f"callback data exceeds {MAX_CALLBACK_BYTES} bytes: {data!r}")
    return data


def decode(data: str) -> Callback:
    """Parse callback data produced by :func:`encode`.

    Raises:
        CallbackDataError: wrong version, unknown namespace, or a malformed body.
    """
    parts = data.split(":")
    if len(parts) < 3:
        raise CallbackDataError(f"callback data is too short: {data!r}")
    version, namespace, action = parts[0], parts[1], parts[2]
    argument = ":".join(parts[3:]) if len(parts) > 3 else None
    if version != CALLBACK_VERSION:
        raise CallbackDataError(f"unsupported callback version {version!r}")
    if namespace not in NAMESPACES:
        raise CallbackDataError(f"unknown callback namespace {namespace!r}")
    if not _ACTION.fullmatch(action):
        raise CallbackDataError(f"invalid callback action {action!r}")
    if argument is not None and not _ARGUMENT.fullmatch(argument):
        raise CallbackDataError(f"invalid callback argument {argument!r}")
    return Callback(namespace=namespace, action=action, argument=argument)


def poll_action(action: str, poll_id: int | None = None) -> str:
    """Encode a poll-namespace callback."""
    return encode(NAMESPACE_POLL, action, poll_id)


def schedule_action(action: str, schedule_id: int | None = None) -> str:
    """Encode a schedule-namespace callback."""
    return encode(NAMESPACE_SCHEDULE, action, schedule_id)


WIZARD_BACK = "back"
WIZARD_CANCEL = "cancel"
WIZARD_NEXT = "next"
WIZARD_DONE = "done"
WIZARD_SKIP = "skip"
WIZARD_ANONYMITY = "anon"
WIZARD_MULTIPLE = "multi"
WIZARD_TARGET = "tgt"
WIZARD_TARGET_PAGE = "tgtp"
WIZARD_KIND = "kind"
WIZARD_WEEKDAY = "wd"
WIZARD_OPTION_REMOVE = "opt"
WIZARD_ADD_OPTION = "add"
WIZARD_CONFIRM = "create"


def wizard_action(action: str, argument: str | int | None = None) -> str:
    """Encode a wizard-namespace callback."""
    return encode(NAMESPACE_WIZARD, action, argument)
