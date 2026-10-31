# telegram-auto-poll

A private Telegram bot that publishes native polls to configured groups and channels on a saved
schedule. Administrators manage polls, schedules, and target chats entirely through direct messages
with the bot — there is no web interface.

> **Status:** constitution v1.0.0 is ratified and SPEC-001 is implemented: all 17 admin commands,
> the poll wizard, scheduling, delivery with retries and recovery, observability, the runtime, and the
> packaging files are in place. **G3 code review is requested** (`specs/001-core-bot/traceability.md`
> carries the FR/invariant mapping). Local gates are green (**737 tests, 100 % line and branch**,
> ruff/mypy clean); the container build, Compose healthcheck, and the live Telegram check (T-054) are
> **not yet verified** because no Docker daemon/Compose plugin and no dedicated test token were
> available in the implementation environment.

## Quickstart

```bash
cp .env.example .env            # set TELEGRAM_BOT_TOKEN and ADMIN_USER_IDS
docker compose build
docker compose up -d bot
curl -fsS http://127.0.0.1:8080/livez
curl -fsS http://127.0.0.1:8080/healthz
docker compose logs -f bot
```

Then DM the bot from an allowlisted account and send `/start`, `/addchat` (forward a message from the
target chat), `/newpoll`, and `/status`. The database and pre-migration backups live in the
`poll-data` volume under `/data`.

## What it does

- Stores many reusable poll definitions: question, optional description, 2–12 options, anonymity, and
  multiple-answer behavior.
- Schedules each poll daily, weekly, monthly, or one-off, in a per-schedule IANA timezone.
- Delivers each due poll once to every active target chat, with bounded retries and duplicate
  suppression.
- Skips occurrences missed beyond a grace window rather than spamming chats after downtime.
- Registers target groups, supergroups, and channels by forwarded message or chat ID.
- Reports health, metrics, delivery history, and audit events.

## Architecture at a glance

A single asyncio process hosts four components: the Telegram bot (long polling), the APScheduler
scheduler, a rate-limited delivery worker, and a loopback-only aiohttp sidecar for `/livez`,
`/healthz`, and `/metrics`. SQLite in WAL mode is the single source of truth. The app runs as one
container on one VPS; **running more than one instance against the same bot token is forbidden.**

See [`CONSTITUTION.md`](CONSTITUTION.md) for the full architecture, domain model, requirements, and
engineering rules.

## Stack

- Python 3.12, `uv`, fully typed with `mypy --strict`
- `python-telegram-bot` (long polling)
- APScheduler 3.x (`AsyncIOScheduler`, in-memory jobs rebuilt from SQLite)
- SQLAlchemy 2.0 async + `aiosqlite`, Alembic migrations
- SQLite (WAL) in a Docker named volume
- `aiohttp` + `prometheus-client` for health and metrics
- `pytest` with 100% line and branch coverage, `ruff`, `bandit`, `pip-audit`, `gitleaks`

## Planned quickstart

These commands describe the intended developer flow and will work once the first implementation
feature is delivered:

```bash
cp .env.example .env        # set TELEGRAM_BOT_TOKEN and ADMIN_USER_IDS
uv sync --frozen
uv run pytest
docker compose up -d
curl -fsS http://127.0.0.1:8080/healthz
```

## Admin onboarding

1. Create a bot with [@BotFather](https://t.me/botfather) and put the token in `.env`.
2. Put your numeric Telegram user ID in `ADMIN_USER_IDS`.
3. Start the bot, open a **direct message** with it, and press **Start**.
4. Send `/addchat`, then forward a message from the target group/channel or paste its numeric ID.
5. Send `/newpoll` and follow the wizard.
6. Use `/status` to confirm schedules and delivery health.

Admin commands are accepted **only in direct messages** from allowlisted user IDs. The bot ignores
commands in groups and channels.

## Documentation

| Document | Purpose |
|---|---|
| [`CONSTITUTION.md`](CONSTITUTION.md) | Mission, architecture, domain rules, standards, and the 17-step SDD workflow. |
| [`docs/runbook.md`](docs/runbook.md) | Deploy, upgrade, rollback, backup, restore, and incident procedures. |
| [`docs/adr/`](docs/adr/README.md) | Architecture decision records. |
| [`specs/`](specs/README.md) | Feature specs, plans, and task lists. |
| [`AGENTS.md`](AGENTS.md) | Operating instructions for AI agents working in this repository. |
| [`SECURITY.md`](SECURITY.md) | Private vulnerability reporting. |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history. |

## Repository layout

```
CONSTITUTION.md     project-wide rules and process
specs/              one directory per feature (spec, plan, tasks)
docs/adr/           architecture decision records
docs/runbook.md     operational procedures
.github/            issue and PR templates
src/                application code (added during implementation)
tests/              unit, integration, and e2e tests
```

## License

MIT. See [`LICENSE`](LICENSE).
