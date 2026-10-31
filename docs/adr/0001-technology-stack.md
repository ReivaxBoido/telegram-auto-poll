# ADR-0001: Technology stack and runtime architecture

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Decision owner | Project maintainers |
| Supersedes | None |
| Related | SPEC-001 (planned), CONSTITUTION.md §3–§4 |

## Context

`telegram-auto-poll` is a private operational bot with a small, well-bounded workload: store reusable
poll definitions, evaluate saved schedules, and send native Telegram polls to a handful of groups and
channels. It must be operable by one person on a single VPS, with strong correctness around time zones
and duplicate avoidance.

The initial constraints were:

- Python, Dockerized.
- No public web surface; administration is via Telegram DM.
- SQLite is acceptable; zero-ops is preferred.
- Scheduled delivery must be reliable and observable.
- The target scale is tens of chats and hundreds of polls, not thousands per second.

## Decision

We will build a **single asyncio Python 3.12 process** with:

- `python-telegram-bot` in **long-polling** mode (no webhook, no public ingress).
- `APScheduler` 3.x `AsyncIOScheduler` with an **in-memory job store**; SQLite is the source of truth
  and jobs are rebuilt from it at startup and after mutations.
- SQLAlchemy 2.0 async ORM over `aiosqlite`, with SQLite in WAL mode and Alembic migrations.
- A loopback-only `aiohttp` sidecar serving `/livez`, `/healthz`, and Prometheus `/metrics`.
- `pydantic-settings` for validated environment configuration and standard-library JSON logging.
- `uv` with a committed lockfile for deterministic builds.
- A multi-stage Docker image and Docker Compose with one service, one named volume, and one replica.

## Alternatives considered

| Alternative | Pros | Cons | Why not chosen |
|---|---|---|---|
| Webhook mode behind Caddy/Traefik | Lower latency, no polling process | Requires public domain, TLS, ingress, and secret-path handling | No web surface is desired; long polling is sufficient for admin commands. |
| FastAPI + web admin UI | Rich editing experience | Adds auth, sessions, CSRF, frontend, and a public attack surface | Administration is Telegram DM only (ADR-0002). |
| `aiogram` instead of `python-telegram-bot` | Strong async API, popular | Smaller typed ecosystem for this use; PTB’s conversation model fits wizards | PTB is mature and sufficient; switching has no material benefit. |
| `cron`/systemd timers + a CLI sender | Dead simple scheduling | No per-poll timezone/DST model, poor observability, hard to edit at runtime | Cannot meet the domain model or admin UX. |
| APScheduler persistent job store | Jobs survive restart | Two sources of truth; drift between jobs and schedule rows | DB rebuild is simpler and testable. |
| PostgreSQL | Strong concurrency, mature ops | Extra service, backups, connection handling | Single-writer SQLite is sufficient at target scale. |
| Redis/Celery queue | Durable retries, workers | Extra infrastructure, serialization, operational burden | In-process bounded sender with DB-backed retries is enough. |
| SQLite `create_all()` | No migration overhead | No schema history, unsafe upgrades | Migrations are required for a live service. |

## Consequences

### Positive

- One process, one container, one data file; minimal operational surface.
- No inbound network exposure and no web authentication to design or defend.
- Deterministic tests: scheduling and Telegram are behind ports and can be faked.
- Database-backed idempotency and retries survive restarts.

### Negative

- **Hard single-instance limit.** Horizontal scaling is not possible without a redesign.
- SQLite serializes writes; long transactions must be avoided.
- APScheduler jobs are ephemeral; correctness depends on rebuilding from the database.
- Long polling has slightly higher update latency than webhooks and holds one connection.

### Neutral / follow-up work

- Add an ADR before introducing PostgreSQL, Redis, a webhook, or a second replica.
- Add a distributed lock or leader election if multi-instance ever becomes necessary.
- Revisit the scheduler if the occurrence model outgrows APScheduler triggers.

## Compliance and verification

- `CONSTITUTION.md` §3 and §4 encode the architecture and stack.
- Tests use a fake clock and fake sender; no network in unit/integration tests.
- CI runs migrations on empty and previous schemas and builds the Docker image.
- The single-instance lock is covered by an integration test.

## Links

- Issue: TBD
- Related: ADR-0002
