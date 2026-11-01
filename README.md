# telegram-auto-poll

A private Telegram bot that publishes **CountMeIn-style sign-up polls** to registered groups and
channels. An allowlisted administrator creates a poll in a direct message, publishes it to one or
more chats, and members tap an option to add or remove their own first name. The bot edits the
message live so everyone sees the current list and count.

> **Status:** v0.3.0-alpha. SPEC-003 (core sign-up polls, manual publishing, live responses,
> close/reopen/delete, retention) is implemented. Scheduling returns in **SPEC-004 / v0.4.0**; inline
> mode and poll editing are **SPEC-005 / v0.5.0**. This alpha is an integration milestone, not a
> production replacement for the previous scheduled-native-poll release.

## Quickstart

```bash
cp .env.example .env            # set TELEGRAM_BOT_TOKEN and ADMIN_USER_IDS
docker compose build
docker compose up -d bot
curl -fsS http://127.0.0.1:8080/livez
curl -fsS http://127.0.0.1:8080/healthz
docker compose logs -f bot
```

Then DM the bot from an allowlisted account and send `/start`, `/addchat` (forward a message from
the target chat), `/newpoll`, and `/publish`. The database and pre-migration backups live in the
`poll-data` volume under `/data`.

## What it does

- Stores many sign-up polls: a title (1–300 characters) and 1–10 options (each 1–100 characters).
- Publishes a poll to one or more registered groups, supergroups, or channels.
- Lets **any member** of a target chat toggle their own first name on any option; multi-select is
  allowed.
- Edits every message for the poll instance so the name list and count stay current, coalescing
  rapid taps to respect Telegram edit limits.
- Supports close, reopen, refresh, and delete. Deleting removes response data and redacts messages.
- Keeps responses global to one **instance**: a poll published to several chats shares one list;
  each new occurrence (SPEC-004) starts fresh.
- Reports health, metrics, and recent failures; stores no usernames, last names, or native poll
  results.

## Commands

| Command | Purpose |
|---|---|
| `/start` `/help` `/version` `/status` | Menu, help, build, health, counts, recent failures. |
| `/chats` `/addchat` `/removechat` | Manage registered target chats. |
| `/polls` `/poll <id>` | List polls and inspect a poll. |
| `/newpoll` | Create a poll with a guided wizard. |
| `/publish <id>` | Select target chats and publish a new instance. |
| `/close <id>` `/reopen <id>` | Close or reopen the latest instance. |
| `/refresh <id>` | Retry failed messages and re-render the latest instance. |
| `/responses <id> [option] [page]` | Show the full name list for one option. |
| `/deletepoll <id>` | Delete a poll, remove responses, and redact messages. |
| `/cancel` | Cancel the open wizard or publish session. |

Admin commands work only in a **private chat** from a user in `ADMIN_USER_IDS`. Vote buttons work
for ordinary chat members and are validated against the stored message, open instance, and option.

## Architecture at a glance

A single asyncio process hosts the Telegram bot (long polling), the posting worker, the response
broadcaster, a retention cron, and a loopback-only aiohttp sidecar for `/livez`, `/healthz`, and
`/metrics`. SQLite in WAL mode is the single source of truth. The app runs as one container on one
VPS; **running more than one instance against the same bot token is forbidden.**

The core model is:

- **Poll** — title plus ordered options.
- **Instance** — one publication of a poll; responses belong to the instance.
- **Message** — one bot message per instance/chat, with delivery state.
- **Response** — a user's membership in one option, with a first-name snapshot.

See [`CONSTITUTION.md`](CONSTITUTION.md) for the full architecture, domain rules, and engineering
rules, and [`specs/003-countmein-core/`](specs/003-countmein-core/spec.md) for this release.

## Stack

- Python 3.12, `uv`, fully typed with `mypy --strict`
- `python-telegram-bot` (long polling)
- APScheduler 3.x (daily retention cron; due-schedule evaluation returns in SPEC-004)
- SQLAlchemy 2.0 async + `aiosqlite`, Alembic migrations
- SQLite (WAL) in a Docker named volume
- `aiohttp` + `prometheus-client` for health and metrics
- `pytest` with 100% line and branch coverage, `ruff`, `bandit`, `pip-audit`, `gitleaks`

## Admin onboarding

1. Create a bot with [@BotFather](https://t.me/botfather) and put the token in `.env`.
2. Put your numeric Telegram user ID in `ADMIN_USER_IDS`.
3. Start the bot, open a **direct message** with it, and press **Start**.
4. Send `/addchat`, then forward a message from the target group/channel or paste its numeric ID.
5. Send `/newpoll`, enter a title and options, then `/publish <id>` and select the target chats.
6. Use `/status` to confirm health and counts.

## Upgrading from the native-poll release

Migration `0002_countmein_core` is a **destructive reset** of the native-poll tables. It aborts if
the old tables contain rows unless `ALLOW_DESTRUCTIVE_MIGRATION=true` is set for that one run. Take a
verified backup first; rollback means restoring the pre-migration backup and the previous image. See
[`docs/runbook.md`](docs/runbook.md).

## Development

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov --cov-branch --cov-fail-under=100
docker build -t telegram-auto-poll:local .
```

The suite currently has **328 tests at 100% line and branch coverage**.

## Documentation

| Document | Purpose |
|---|---|
| [`CONSTITUTION.md`](CONSTITUTION.md) | Mission, architecture, domain rules, standards, and the 17-step SDD workflow. |
| [`docs/runbook.md`](docs/runbook.md) | Deploy, upgrade, rollback, backup, restore, deletion, and incident procedures. |
| [`docs/adr/`](docs/adr/README.md) | Architecture decision records. |
| [`specs/`](specs/README.md) | Feature specs, plans, and task lists. |
| [`AGENTS.md`](AGENTS.md) | Operating instructions for AI agents working in this repository. |
| [`SECURITY.md`](SECURITY.md) | Private vulnerability reporting. |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history. |

## License

MIT. See [`LICENSE`](LICENSE).
