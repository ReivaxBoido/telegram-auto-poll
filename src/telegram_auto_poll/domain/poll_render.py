"""Pure rendering of the sign-up poll message (SPEC-003 FR-326 - FR-328).

The layout follows CountMeInBot:

```
Wed training @ MPSH

0700-1030 (11👥)
Xavier
rui
...

1045-1330 (16👥)
Xavier
...

👥 19 people responded
```

The renderer knows nothing about Telegram beyond the callback payload shape: it returns text and
``PollButton`` objects. The bot adapter turns the buttons into an inline keyboard and encodes the
callback data.

The text never exceeds Telegram's 4,096-character limit. The only variable-length part is the name
list, so the renderer reduces the number of names per option until the message fits; it never
truncates the title or an option label.
"""

from __future__ import annotations

from collections.abc import Sequence

from .callbacks import vote
from .entities import OptionResponses, Poll, PollButton, PollInstance, PollRender
from .enums import InstanceStatus, PollStatus

MAX_MESSAGE_LENGTH = 4_096
BUTTON_LABEL_MAX = 60
ELLIPSIS = "…"
PEOPLE = "👥"
#: Name limits tried in order. The configured limit is added first by ``_limits``.
NAME_LIMITS = (50, 30, 20, 15, 10, 5, 3, 1, 0)


def _truncate_button(label: str) -> str:
    if len(label) <= BUTTON_LABEL_MAX:
        return label
    return label[: BUTTON_LABEL_MAX - 1] + ELLIPSIS


def _option_lines(option: OptionResponses, limit: int) -> list[str]:
    count = len(option.respondents)
    lines = [f"{option.option.label} ({count}{PEOPLE})"]
    names = [name for _, name in option.respondents[:limit]]
    lines.extend(names)
    remaining = count - len(names)
    if remaining > 0:
        lines.append(f"... +{remaining} more")
    return lines


def _total_line(options: Sequence[OptionResponses]) -> str:
    distinct = {user_id for option in options for user_id, _ in option.respondents}
    if not distinct:
        return f"{PEOPLE} Nobody responded"
    if len(distinct) == 1:
        return f"{PEOPLE} 1 person responded"
    return f"{PEOPLE} {len(distinct)} people responded"


def _render_text(
    poll: Poll,
    instance: PollInstance,
    options: Sequence[OptionResponses],
    *,
    names_per_option: int,
    closed: bool,
) -> str:
    lines = [poll.title, ""]
    for option in options:
        lines.extend(_option_lines(option, names_per_option))
        lines.append("")
    lines.append(_total_line(options))
    if closed:
        lines.append("Voting closed.")
    return "\n".join(lines)


def _limits(names_per_option: int) -> tuple[int, ...]:
    if names_per_option <= 0:
        return (0,)
    return tuple(limit for limit in NAME_LIMITS if limit <= names_per_option)


def render_poll_message(
    poll: Poll,
    instance: PollInstance,
    options: Sequence[OptionResponses],
    *,
    names_per_option: int,
    max_length: int = MAX_MESSAGE_LENGTH,
) -> PollRender:
    """Render one poll message, reducing the name list until it fits.

    Raises:
        ValueError: the instance or an option has not been persisted, or the fixed content alone
            exceeds ``max_length`` (which the validation limits make unreachable).
    """
    if instance.id is None:
        raise ValueError("a poll message needs a persisted instance id")
    if poll.status is PollStatus.DELETED or instance.status is InstanceStatus.DELETED:
        return PollRender(text="This poll was deleted.", closed=True)

    closed = instance.status is InstanceStatus.CLOSED
    for limit in _limits(names_per_option):
        text = _render_text(poll, instance, options, names_per_option=limit, closed=closed)
        if len(text) <= max_length:
            if closed:
                return PollRender(text=text, closed=True)
            buttons = tuple(
                PollButton(
                    label=_truncate_button(option.option.label),
                    callback_data=vote(instance.id, _option_id(option)),
                )
                for option in options
            )
            return PollRender(text=text, buttons=buttons)

    raise ValueError(
        f"poll message exceeds {max_length} characters even without names; check the poll limits"
    )


def _option_id(option: OptionResponses) -> int:
    if option.option.id is None:
        raise ValueError("a vote button needs a persisted option id")
    return option.option.id


def keyboard_rows(render: PollRender) -> tuple[tuple[tuple[str, str], ...], ...]:
    """Group vote buttons two per row."""
    if not render.buttons:
        return ()
    rows: list[tuple[tuple[str, str], ...]] = []
    current: list[tuple[str, str]] = []
    for button in render.buttons:
        current.append((button.label, button.callback_data))
        if len(current) == 2:
            rows.append(tuple(current))
            current = []
    if current:
        rows.append(tuple(current))
    return tuple(rows)
