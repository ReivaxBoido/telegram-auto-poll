# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project constitution v1.0.0 (ratified at G2 on 2026-10-31): mission, architecture, domain model,
  requirements, security, quality gates, and the 17-step SDD workflow.
- SDD scaffold: spec/plan/task templates, ADR process and initial ADRs, operations runbook, issue and
  pull-request templates, and agent instructions.
- SPEC-001 (core bot, issue #1): approved spec, technical plan, and 55-task breakdown (G2, 2026-10-31).
- Initial repository documentation and MIT license.
- Python 3.12 project skeleton managed by `uv`: `pyproject.toml`, committed `uv.lock`, strict
  `ruff`/`mypy`/`pytest` configuration, and the `src/telegram_auto_poll` package layout.
- Domain layer: immutable entities, enums, the error hierarchy, input validators, and pure schedule
  mathematics covering daily/weekly/monthly/one-off rules, DST gaps and folds, leap day, and month
  boundaries.
- Configuration: `pydantic-settings` model that validates every variable, redacts the bot token, and
  reports the offending variable with its expected format.
- Clock and sleeper ports with system adapters, deterministic test doubles, a socket-blocking test
  fixture, and architectural invariant tests (domain purity, no `datetime.now()` outside the clock).
- Persistence: SQLAlchemy 2.0 models for the six constitutional tables with a metadata naming
  convention, an async SQLite engine that applies `foreign_keys`, WAL, `synchronous=NORMAL`, and
  `busy_timeout` to every connection, and a generated Alembic revision (`0001`).
- Repositories for chats, polls, schedules, deliveries (idempotent creation via the partial unique
  index, claiming, retry/failure/skip transitions, batching) and the append-only audit log.
- Startup migration that backs up the database before upgrading, keeps the five newest backups, and
  leaves the backup behind if the upgrade fails; retention helpers prune in fixed-size batches.
- Single-instance `flock` guard on `<DATA_DIR>/app.lock`; a second process exits with code 3 without
  opening the database.
- Services: chat registration/reactivation/deactivation (with pending-delivery skipping and orphaned
  schedule pausing), atomic poll creation with an optional schedule, schedule pause/resume/delete and
  five-occurrence previews, delivery planning and dispatch with bounded retries, `429` handling,
  sanitized errors, manual sends, and startup recovery of `sending` rows.
- Telegram pacing (1/s per chat, 20/min per group, 25/s global), an audit service that writes inside
  the caller's transaction, and batched 90-day retention with a WAL checkpoint hook.
- Telegram adapters: `TelegramPollSender` with retryable/terminal error classification and no parse
  mode, plus `TelegramChatGateway` and forwarded-message extraction for both Bot API shapes.
- Scheduler: a reconciling job planner (add/remove/re-time, idempotent), an APScheduler `DateTrigger`
  backend with a coalesced safety tick, and a monotonic heartbeat for readiness checks.
- Delivery worker: `SEND_CONCURRENCY` loops that claim one row at a time, log and restart after a
  crash, finish in-flight sends on shutdown, and never leak tasks.
- Startup recovery: ambiguous `sending` rows fail without retry and raise one admin summary; overdue
  occurrences are skipped or sent inside the grace window by the planning pass.
- Admin authorization: private-chat and allowlist checks for messages and callbacks, versioned and
  namespaced callback validation, and a per-admin token bucket (20/min commands, 5/min `/sendnow`)
  that refuses without mutating state.
- Versioned callback codec (`v1:<namespace>:<action>[:<argument>]`, 64-byte ceiling), plain-text
  renderers bounded by Telegram's 4,096-character limit, and layout-validated inline keyboards
  (8 rows × 4 buttons).
- Admin command handlers: all 17 commands, with a single admission path (private chat, allowlist,
  budget), `/status` health and counts, paginated listings, chat registration by forward or id, and
  confirmations for remove/send/delete.
- Observability: JSON logging with token redaction, a Prometheus registry exposing every
  documented `tap_*` metric with low-cardinality labels only, and an aiohttp sidecar with `/livez`,
  `/healthz` (503 naming the database or scheduler reason), and `/metrics`.
- PTB application wiring: private-chat message and callback handlers, an error handler, a
  `BotResponder` backed by `send_message`/`answer_callback_query`, and the command menu registered
  with `BotCommandScopeAllPrivateChats`.
- `/newpoll` text-driven wizard handler: starts a session, drives the engine step by step, commits
  through `PollService.create`, keeps the session on a refused commit, and removes options via a
  callback. Unexpected handler errors return a correlation ID.
- Pure new-poll wizard engine: total over every `(step, event)` pair, with back/cancel/timeout,
  per-step validation reusing the domain validators, day/weekly/monthly/one-off schedule capture,
  timezone conversion for one-off instants, next-five previews, and a single `CommitPoll` draft only
  after confirmation. Sessions live in a TTL store (15 minutes) with a lazy janitor.

- Packaging: multi-stage `Dockerfile` (non-root 10001, tzdata, healthcheck, OCI labels), hardened
  `docker-compose.yml` (read-only root, `cap_drop: ALL`, loopback health port, `/data` volume),
  GitHub Actions CI (ruff, mypy, pytest, bandit, pip-audit, gitleaks, Docker build, Trivy, GHCR
  publish on tags), and Dependabot for pip, Actions, and Docker.

- Container verification: image builds (61.3 MB compressed), Compose config validates, the
  container reports healthy, `/livez` and `/healthz` return 200, and `/metrics` exposes the `tap_*`
  series. Added a one-shot `permissions` service so the non-root user can write the `/data` volume.

- Retention is now scheduled: a daily 03:15 UTC APScheduler cron job runs `RetentionService`, records
  the deleted-row counters, and checkpoints the WAL; the planner ignores non-date jobs.

### Changed

- SPEC-001 status moved from `Draft` to `Approved`; no production code existed at G2.

### Fixed

- Nothing yet.

### Security

- Nothing yet.

## Release conventions

- `feat` → minor, `fix`/`perf` → patch, breaking change → major.
- Release tags are annotated `vMAJOR.MINOR.PATCH` on `main`.
- Each release links its spec(s), migration notes, and rollback guidance.
