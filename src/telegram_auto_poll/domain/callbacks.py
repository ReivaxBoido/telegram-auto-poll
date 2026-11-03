"""Versioned callback data shared by the bot and the posting worker (SPEC-003 FR-317).

Envelope: ``v1:<namespace>:<action>[:<argument>]``, at most 64 bytes. Telegram truncates longer
payloads silently, so encoding refuses them instead of producing an unclickable button.

Namespaces:

* ``v`` — participant vote (instance and option identifiers)
* ``p`` — admin poll action (publish, close, reopen, refresh, delete, responses)
* ``t`` — admin publish target selection
* ``w`` — admin wizard
* ``c`` — admin confirmation
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CALLBACK_VERSION = "v1"
MAX_CALLBACK_BYTES = 64

NAMESPACE_VOTE = "v"
NAMESPACE_POLL = "p"
NAMESPACE_TARGET = "t"
NAMESPACE_WIZARD = "w"
NAMESPACE_CONFIRM = "c"

NAMESPACES = frozenset(
    {NAMESPACE_VOTE, NAMESPACE_POLL, NAMESPACE_TARGET, NAMESPACE_WIZARD, NAMESPACE_CONFIRM}
)

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


def vote(instance_id: int, option_id: int) -> str:
    """Callback data for toggling one option of one instance."""
    return encode(NAMESPACE_VOTE, "toggle", f"{instance_id}_{option_id}")


def poll_action(action: str, poll_id: int) -> str:
    """Callback data for an admin poll action."""
    return encode(NAMESPACE_POLL, action, poll_id)


def target_toggle(poll_id: int, chat_id: int) -> str:
    """Callback data for toggling one target chat during publish."""
    return encode(NAMESPACE_TARGET, "toggle", f"{poll_id}_{chat_id}")


def target_page(poll_id: int, page: int) -> str:
    """Callback data for paging the publish target list."""
    return encode(NAMESPACE_TARGET, "page", f"{poll_id}_{page}")


def target_done(poll_id: int) -> str:
    """Callback data for confirming the publish target selection."""
    return encode(NAMESPACE_TARGET, "done", poll_id)


def target_cancel(poll_id: int) -> str:
    """Callback data for cancelling the publish target selection."""
    return encode(NAMESPACE_TARGET, "cancel", poll_id)


def wizard_action(action: str, argument: str | int | None = None) -> str:
    """Callback data for a wizard step."""
    return encode(NAMESPACE_WIZARD, action, argument)


def confirm(action: str, entity_id: int) -> str:
    """Callback data for a destructive confirmation."""
    return encode(NAMESPACE_CONFIRM, action, entity_id)
