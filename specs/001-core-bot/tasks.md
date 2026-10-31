# Tasks: SPEC-001 — Core bot, scheduled poll delivery

Each task is independently verifiable, scoped to one day or less, and includes its tests and docs.
Order is dependency order. Check a task only when its acceptance check passes in CI.

## Conventions

- ID: `T-001`, `T-002`, …
- Type: `test`, `feat`, `migration`, `docs`, `chore`, `ops`
- Every implementation task is preceded by its test task.
- "Covers" uses spec IDs (`FR-1xx`, `NFR-1xx`) and constitution invariants (`I-n`).

## Prerequisite (needs human approval)

- [x] **T-000** (chore) Approve network access for `uv lock` / `uv sync --frozen`.
  - Why: the constitution mandates `uv` with a committed lockfile; generating it downloads package
    metadata and wheels.
  - Acceptance: explicit human approval recorded on issue #1 or the PR.
  - Blocked-by: none. Blocks: T-001, T-053.

## Phase 1 — Scaffolding and contracts (T-001–T-010)

- [x] **T-001** (chore) Create `pyproject.toml` and commit `uv.lock`.
  - Files: `pyproject.toml`, `uv.lock`
  - Contents: Python 3.12, runtime deps (`python-telegram-bot`, `APScheduler`, `SQLAlchemy`,
    `aiosqlite`, `alembic`, `pydantic-settings`, `aiohttp`, `prometheus-client`, `tzdata`), dev group
    (`pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `bandit`, `pip-audit`).
  - Acceptance: `uv sync --frozen` succeeds; no unpinned installs.
  - Covers: constitution §4.1

- [x] **T-002** (chore) Configure tooling in `pyproject.toml`.
  - Files: `pyproject.toml`
  - Contents: ruff (line 100, `py312`, rule families from §4.3), mypy strict, pytest
    (`asyncio_mode=auto`, `--strict-markers`, `--strict-config`), coverage (branch, source, omit
    migrations, `fail_under=100`).
  - Acceptance: `ruff format --check .`, `ruff check .`, `mypy src` run and report no config errors.
  - Covers: NFR-101, §4.3

- [x] **T-003** (chore) Create the package skeleton.
  - Files: `src/telegram_auto_poll/__init__.py`, `__main__.py`, empty `bot/`, `domain/`, `db/`,
    `services/`, `scheduler/`, `observability/`, `tests/{unit,integration}`, `tests/fakes.py`,
    `tests/factories.py`, `tests/conftest.py`
  - Acceptance: `python -m telegram_auto_poll` exits with a clear "not implemented" message; socket
    blocker fixture is active (a test that opens a socket fails).
  - Covers: §13.1

- [x] **T-004** (test) Config validation tests.
  - Files: `tests/unit/test_config.py`
  - Cases: unset credential, empty or invalid admin allowlist, bad `HEALTH_PORT`, invalid timezone, invalid
    log level, defaults applied, secret never in `repr`/errors.
  - Acceptance: tests fail before T-005 for the expected reasons.
  - Covers: FR-101, §8.1

- [x] **T-005** (feat) Implement configuration.
  - Files: `src/telegram_auto_poll/config.py`
  - Contents: `Settings` (pydantic-settings), field validators, `RedactedSecret`, `ConfigurationError`
    mapping, `get_settings()`.
  - Acceptance: T-004 passes; `mypy --strict` clean.
  - Covers: FR-101

- [x] **T-006** (test) Domain entities, enums, errors, and validation tests.
  - Files: `tests/unit/test_validation.py`, `tests/unit/test_entities.py`
  - Cases: question/description/option boundaries (0/1/max/max+1), 1/2/12/13 options, duplicate and
    blank options (case-folded, trimmed), invalid timezone, invalid weekday, day 0/32, end before
    start, past one-off, invalid chat type.
  - Acceptance: each boundary row corresponds to one test.
  - Covers: I-1, I-2, I-3, I-4, I-5, I-6, FR-117, FR-122

- [x] **T-007** (feat) Implement domain entities, enums, errors, validation.
  - Files: `src/telegram_auto_poll/domain/{entities,enums,errors,validation}.py`
  - Acceptance: T-006 passes; `domain/` imports only the standard library.
  - Covers: §5, §13.3

- [x] **T-008** (test) Schedule math tests.
  - Files: `tests/unit/test_schedule_math.py`
  - Cases: daily across spring-forward and fall-back; weekly with multiple ISO weekdays across a month
    boundary; monthly days 1/28/29/30/31 incl. February leap/non-leap; one-off UTC conversion; start
    inclusive; end inclusive; expired; year boundary; `Australia/Lord_Howe` half-hour shift; next-five
    ordering and no duplicates; monotonic `next_run_at_utc`.
  - Acceptance: every case fails before T-009 for the expected reason.
  - Covers: I-3–I-8, NFR-103, §5.5

- [x] **T-009** (feat) Implement schedule math.
  - Files: `src/telegram_auto_poll/domain/schedule_math.py`
  - Functions: `next_occurrence(schedule, after)`, `next_occurrences(schedule, after, count)`,
    `occurrence_at_or_before(...)` helpers; DST resolution via zoneinfo round-trip; monthly skip.
  - Acceptance: T-008 passes; branch coverage of the module is 100%.
  - Covers: FR-121, FR-123

- [x] **T-010** (feat) Implement `Clock` and `Sleeper` ports and system adapters, plus fakes.
  - Files: `src/telegram_auto_poll/clock.py`, `src/telegram_auto_poll/domain/ports.py`,
    `tests/fakes.py`
  - Acceptance: `FakeClock` rejects naive datetimes; `FakeSleeper` records waits and returns instantly;
    unit test for the system clock's UTC awareness.
  - Covers: §5.5 item 10, NFR-105

## Phase 2 — Persistence (T-011–T-017)

- [x] **T-011** (test) Schema and constraint integration tests.
  - Files: `tests/integration/test_schema.py`
  - Cases: unique `telegram_chat_id`; partial unique delivery index (duplicate ignored, manual allowed);
    FK `RESTRICT` and `SET NULL`; JSON validity and option-count checks; boolean checks; timezone
    columns; `alembic_version == head` after upgrade on an empty DB.
  - Acceptance: tests fail before T-012/T-013 exist.
  - Covers: I-2, I-9, I-10, I-11, FR-127

- [x] **T-012** (feat) Implement SQLAlchemy models, metadata convention, and engine.
  - Files: `src/telegram_auto_poll/db/{models,engine}.py`
  - Contents: `Mapped[]` models for all six tables, naming convention, `create_async_engine`,
    `PRAGMA` connect listener, `async_sessionmaker`, `check_database()`.
  - Acceptance: metadata renders the expected DDL; PRAGMAs applied on every connection.
  - Covers: FR-102

- [x] **T-013** (migration) Add Alembic scaffolding and the initial migration.
  - Files: `alembic.ini`, `src/telegram_auto_poll/db/migrations/{env.py,script.py.mako}`, `versions/0001_initial.py`
  - Contents: six tables, checks, indexes, partial unique index via `sqlite_where`.
  - Acceptance: `alembic upgrade head` on an empty file creates all objects; a second run is a no-op;
    `alembic downgrade base` succeeds (not used in production).
  - Covers: FR-102, I-9, §14.1

- [x] **T-014** (feat) Implement repositories with integration tests.
  - Files: `src/telegram_auto_poll/db/repositories/*.py`, `tests/integration/test_repositories.py`
  - Contents: chat, poll, schedule, delivery, audit repositories; `insert_many_ignore`, `claim_due`,
    deterministic ordering; no commits inside repositories.
  - Acceptance: all repository methods covered; no session leaks; mypy clean.
  - Covers: §13.5

- [x] **T-015** (feat) Startup migration with pre-migration backup and retention.
  - Files: `src/telegram_auto_poll/db/migrate.py`, `tests/integration/test_migrate.py`
  - Contents: backup via `sqlite3.Connection.backup()`, `0600`, keep five newest, `alembic upgrade head`
    programmatically, failure exits non-zero leaving the backup.
  - Acceptance: tests assert order (backup before upgrade), retention count, and failure behavior.
  - Covers: FR-105, FR-124

- [x] **T-016** (feat) Implement the single-instance lock.
  - Files: `src/telegram_auto_poll/app.py` (lock helper), `tests/integration/test_lock.py`
  - Contents: `fcntl.flock(LOCK_EX|LOCK_NB)` on `<DATA_DIR>/app.lock`, exit code 3 on contention,
    released on shutdown.
  - Acceptance: a second process exits non-zero without touching the database.
  - Covers: FR-103, C-1

- [x] **T-017** (test) Migration idempotency and repository transaction-boundary tests.
  - Files: `tests/integration/test_migrate.py`, `tests/integration/test_transactions.py`
  - Cases: upgrade at head is a no-op; service-level rollback leaves no audit row; repository methods
    never commit.
  - Acceptance: all pass; no schema drift between models and migration (`compare_metadata`).
  - Covers: FR-134, §14.1

## Phase 3 — Services and adapters (T-018–T-031)

- [x] **T-018** (test) Chat service tests.
  - Files: `tests/unit/test_chat_service.py`
  - Cases: forwarded chat extraction (`forward_origin.sender_chat`/`chat`, legacy `forward_from_chat`),
    pasted ID parsing, unsupported chat types, channel permission failure, idempotent re-registration,
    reactivation, deactivation skipping pending deliveries, last-target removal pausing schedules.
  - Acceptance: tests encode FR-112–FR-115 and fail before T-019.
  - Covers: FR-112, FR-113, FR-114, FR-115

- [x] **T-019** (feat) Implement the chat service, `ChatGateway` port, and Telegram adapter.
  - Files: `src/telegram_auto_poll/services/chat_service.py`,
    `src/telegram_auto_poll/domain/ports.py`, `src/telegram_auto_poll/bot/telegram_gateway.py`
  - Acceptance: T-018 passes; adapter maps PTB exceptions to `DomainError`/`PermissionDeniedError`.
  - Covers: FR-112, FR-113

- [x] **T-020** (test) Poll service tests.
  - Files: `tests/unit/test_poll_service.py`
  - Cases: create poll + targets + optional schedule in one transaction; invalid option sets rejected;
    audit event written; list pagination; detail with next runs; archived poll cannot be sent.
  - Acceptance: atomicity asserted by a failing repository that rolls back the whole unit.
  - Covers: FR-116, FR-119, I-12

- [x] **T-021** (feat) Implement the poll service.
  - Files: `src/telegram_auto_poll/services/poll_service.py`
  - Acceptance: T-020 passes; no Telegram import in the service.
  - Covers: FR-116, FR-119

- [x] **T-022** (test) Schedule service tests.
  - Files: `tests/unit/test_schedule_service.py`
  - Cases: create each kind; validation of weekdays/day-of-month/one-off; pause sets next null; resume
    recomputes > now; delete hard-deletes only the schedule; preview shows five; last-target rule.
  - Acceptance: every case maps to FR-121–FR-124.
  - Covers: FR-121, FR-122, FR-123, FR-124

- [x] **T-023** (feat) Implement the schedule service.
  - Files: `src/telegram_auto_poll/services/schedule_service.py`
  - Acceptance: T-022 passes; `next_run_at_utc` always derived from `schedule_math`.
  - Covers: FR-121–FR-124

- [x] **T-024** (test) Delivery service tests.
  - Files: `tests/unit/test_delivery_service.py`
  - Cases: idempotent creation; grace boundary (`<=` sends, `>` skips); claim conflict; success stores
    message ID; retryable error schedules backoff; non-retryable fails; exhausted attempts fail; 429
    honors `retry_after` capped at 300; inactive chat/archived poll skipped; manual send not deduped;
    sanitized `last_error`.
  - Acceptance: fake clock/sleeper/sender only; no sleeps.
  - Covers: FR-125, FR-127–FR-133, I-9–I-11

- [x] **T-025** (feat) Implement the delivery service.
  - Files: `src/telegram_auto_poll/services/delivery_service.py`
  - Acceptance: T-024 passes; no network call inside a transaction.
  - Covers: FR-125, FR-127–FR-133

- [x] **T-026** (test) Rate limiter tests.
  - Files: `tests/unit/test_rate_limiter.py`
  - Cases: 1/s per chat; 20/min per group; 25/s global; waiting is delegated to `FakeSleeper`; no busy
    loop.
  - Acceptance: fake-time assertions, no real waits.
  - Covers: FR-130

- [x] **T-027** (feat) Implement the rate limiter.
  - Files: `src/telegram_auto_poll/services/rate_limiter.py`
  - Acceptance: T-026 passes; bounded memory (prunes old timestamps).
  - Covers: FR-130

- [x] **T-028** (test) Sender adapter classification tests.
  - Files: `tests/unit/test_sender.py`
  - Cases: success returns message ID; `RetryAfter` → retryable with `retry_after`; `TimedOut`,
    `NetworkError`, 5xx → retryable; `BadRequest` (chat not found, kicked, no rights) → non-retryable;
    exact `send_poll` kwargs for description/no-description; `parse_mode` is unset so `<b>` is literal.
  - Acceptance: uses a fake PTB `Bot`, no network.
  - Covers: FR-120, FR-129, FR-131

- [x] **T-029** (feat) Implement the `PollSender` port and `TelegramPollSender`.
  - Files: `src/telegram_auto_poll/domain/ports.py`,
    `src/telegram_auto_poll/bot/telegram_sender.py`
  - Acceptance: T-028 passes; error messages sanitized.
  - Covers: FR-129

- [x] **T-030** (test) Audit and retention service tests.
  - Files: `tests/unit/test_audit_service.py`, `tests/unit/test_retention_service.py`
  - Cases: audit row in the same transaction as the mutation; rollback leaves none; retention batches of
    1,000; idempotent second run; only rows older than cutoff; WAL checkpoint called.
  - Acceptance: batch counts asserted.
  - Covers: FR-134, FR-135

- [ ] **T-031** (feat) Implement audit and retention services.
  - Files: `src/telegram_auto_poll/services/{audit_service,retention_service}.py`
  - Acceptance: T-030 passes; retention registered as a 03:15 UTC cron job.
  - Covers: FR-134, FR-135

## Phase 4 — Runtime (T-032–T-037)

- [x] **T-032** (test) Scheduler job planner tests.
  - Files: `tests/unit/test_job_planner.py`
  - Cases: adds missing jobs; removes paused/expired/deleted; re-times changed `next_run_at_utc`; is
    idempotent; handles 1,000 schedules within a sane bound.
  - Acceptance: uses a fake scheduler port; no APScheduler in the test.
  - Covers: FR-125, §3.4

- [x] **T-033** (feat) Implement the scheduler engine and heartbeat.
  - Files: `src/telegram_auto_poll/scheduler/engine.py`
  - Contents: `AsyncIOScheduler`, one `DateTrigger` job per active schedule, interval safety tick
    (`max_instances=1`, `coalesce=True`), `reconcile()`, monotonic heartbeat, gauge refresh.
  - Acceptance: T-032 passes; scheduler starts/stops cleanly.
  - Covers: FR-125, FR-136

- [x] **T-034** (test) Delivery worker tests.
  - Files: `tests/unit/test_worker.py`
  - Cases: bounded concurrency; stops claiming after cancellation; drain within grace; one chat cannot
    starve others; worker crash is logged and the task restarts.
  - Acceptance: fake clock/sleeper/sender; no real concurrency timing assumptions.
  - Covers: FR-129, FR-130, FR-126

- [x] **T-035** (feat) Implement the delivery worker.
  - Files: `src/telegram_auto_poll/scheduler/worker.py`
  - Acceptance: T-034 passes; no unbounded queue.
  - Covers: FR-129, FR-130

- [x] **T-036** (test) Startup recovery tests.
  - Files: `tests/integration/test_recovery.py`
  - Cases: `sending` → `failed` with `ambiguous_outcome_after_restart`; stale occurrence → `skipped`;
    within-grace occurrence remains due and is sent; admin summary sent once; recomputed next runs.
  - Acceptance: no resend of ambiguous rows.
  - Covers: FR-104, FR-128

- [x] **T-037** (feat) Implement the application lifecycle.
  - Files: `src/telegram_auto_poll/app.py`, `src/telegram_auto_poll/__main__.py`
  - Contents: ordered startup (config → logging → lock → DB → backup/migrate → recovery → sidecar →
    scheduler → polling), `TaskGroup` supervision, signal handlers, graceful shutdown, lock release,
    exit codes.
  - Acceptance: T-036 passes; a `SIGTERM` integration test exits 0 within the grace period; startup on
    a seeded database completes within 15 s.
  - Covers: FR-104, FR-126, NFR-102

## Phase 5 — Bot and admin UX (T-038–T-046)

- [x] **T-038** (test) Auth and admin rate-limit tests.
  - Files: `tests/unit/test_auth.py`
  - Cases: allowlisted DM accepted; non-allowlisted DM refused with the exact generic message; group and
    channel commands ignored; callback actor re-checked; forged namespace refused; 21st command in a
    minute refused; `/sendnow` separate 5/min bucket.
  - Acceptance: no state change on any refusal.
  - Covers: FR-106, FR-107, FR-108

- [x] **T-039** (feat) Implement auth filters and the admin command limiter.
  - Files: `src/telegram_auto_poll/bot/auth.py`
  - Acceptance: T-038 passes.
  - Covers: FR-106, FR-107, FR-108

- [x] **T-040** (test) Callback codec, render, and keyboard tests.
  - Files: `tests/unit/test_callbacks.py`, `tests/unit/test_render.py`
  - Cases: round-trip; version rejection; unknown namespace; every generated key ≤ 64 bytes; pagination
    boundaries (empty, one page, exact multiple, last partial); rendering escapes/limits; 4,096-char cap;
    keyboard ≤ 8 rows and ≤ 4 buttons per row.
  - Acceptance: a property-style loop covers every registered key.
  - Covers: §7.4, §7.6

- [x] **T-041** (feat) Implement the callback codec, renderers, and keyboards.
  - Files: `src/telegram_auto_poll/bot/{callbacks,render,keyboards}.py`
  - Acceptance: T-040 passes.
  - Covers: §7.4, §7.6

- [x] **T-042** (test) Wizard engine tests.
  - Files: `tests/unit/test_wizard_engine.py`
  - Cases: every `(step, event)` transition; back; cancel; timeout; invalid re-prompt preserving priors;
    option add/remove/done; target multi-select; each schedule kind; preview; confirm emits
    `CommitPoll`; one active wizard per admin; no commit before confirm.
  - Acceptance: an exhaustive test asserts every enum pair is handled.
  - Covers: FR-111, FR-116, FR-117, FR-118, FR-121, FR-122

- [x] **T-043** (feat) Implement the wizard engine, state, store, and janitor.
  - Files: `src/telegram_auto_poll/bot/wizards/{states,engine,store}.py`
  - Acceptance: T-042 passes; engine is pure (no I/O).
  - Covers: FR-111, FR-116

- [x] **T-044** (test) Handler tests for every command.
  - Files: `tests/unit/test_handlers.py`, `tests/unit/test_handlers_wizard.py`
  - Cases: `/start`, `/help`, `/version`, `/status` fields, `/chats`, `/addchat`, `/removechat`,
    `/polls`, `/poll`, `/newpoll` full flow, `/schedules`, `/schedule`, `/pause`, `/resume`,
    `/delete_schedule`, `/sendnow`, `/cancel`; confirmations; empty states; error states with
    correlation ID.
  - Acceptance: direct invocation with real `Update`/`Message` and `FakeBot`; no dispatcher, no network;
    `/status` responds within 2 s on a seeded database.
  - Covers: FR-109, FR-112–FR-119, FR-123, FR-124, FR-132, NFR-106

- [x] **T-045** (feat) Implement handlers.
  - Files: `src/telegram_auto_poll/bot/handlers/*.py`
  - Acceptance: T-044 passes; each handler under 50 lines and delegating to a service or the engine.
  - Covers: FR-109, FR-112–FR-119, FR-123, FR-124, FR-132

- [x] **T-046** (feat) Wire the PTB application and command menu.
  - Files: `src/telegram_auto_poll/bot/application.py`
  - Contents: `Application.builder()`, handler registration order, `BotCommandScopeAllPrivateChats`,
    error handler that logs and returns a correlation ID.
  - Acceptance: a unit test asserts the registered handler set and the command-menu scope; `/start` DM
    works end-to-end with `FakeBot`.
  - Covers: FR-109, FR-110

## Phase 6 — Observability (T-047–T-048)

- [x] **T-047** (test) Health, metrics, and logging tests.
  - Files: `tests/integration/test_health.py`, `tests/unit/test_logging.py`
  - Cases: `/livez` 200; `/healthz` 200 when DB and heartbeat are healthy; 503 with reasons on DB
    failure and on stale heartbeat; `/metrics` contains every documented metric and no high-cardinality
    labels; JSON log fields; redaction of a known fake token; `last_error` sanitized.
  - Acceptance: aiohttp test client; no network.
  - Covers: FR-133, FR-136, FR-137, §16.2, §16.3

- [x] **T-048** (feat) Implement health, metrics, and logging.
  - Files: `src/telegram_auto_poll/observability/{health,metrics}.py`,
    `src/telegram_auto_poll/logging_setup.py`
  - Acceptance: T-047 passes; server binds the configured host/port and shuts down cleanly.
  - Covers: FR-136, FR-137

## Phase 7 — Packaging, CI, and verification (T-049–T-055)

- [x] **T-049** (feat) Add the Dockerfile and `.dockerignore`.
  - Files: `Dockerfile`, `.dockerignore`
  - Contents: multi-stage, `python:3.12-slim` pinned by digest, `uv sync --frozen` in builder, runtime
    without compilers, non-root 10001, `tzdata`, `PYTHONUNBUFFERED`, `STOPSIGNAL`, `HEALTHCHECK`,
    OCI labels, `BUILD_VERSION`/`BUILD_SHA`.
  - Acceptance: image builds; container starts and reports healthy; size ≤ 250 MB compressed.
  - Covers: FR-138, NFR-107

- [x] **T-050** (feat) Add `docker-compose.yml` and update `.env.example`.
  - Files: `docker-compose.yml`, `.env.example`
  - Contents: one service, `restart: unless-stopped`, `read_only`, `/data` volume, tmpfs `/tmp`,
    `cap_drop: ALL`, `no-new-privileges`, `user: 10001:10001`, `stop_grace_period: 40s`, json-file
    logging, `127.0.0.1:8080:8080` with `HEALTH_HOST=0.0.0.0`.
  - Acceptance: `docker compose config` valid; healthcheck reaches `/healthz`.
  - Covers: FR-138, §15.2

- [x] **T-051** (ops) Add the CI workflow.
  - Files: `.github/workflows/ci.yml`, `.github/dependabot.yml`
  - Contents: format, lint, mypy, pytest with coverage, gitleaks, bandit, pip-audit, Docker build,
    Trivy, migration check on empty DB, PR title/spec link check; least-privilege permissions; actions
    pinned to SHAs.
  - Acceptance: workflow lints (`actionlint` if available) and passes on the PR.
  - Covers: §11.6, §15.3

- [x] **T-052** (docs) Update README, runbook, and CHANGELOG.
  - Files: `README.md`, `docs/runbook.md`, `CHANGELOG.md`, `specs/001-core-bot/spec.md` (status)
  - Contents: real quickstart, admin onboarding, first-run checklist, backup/restore commands verified
    against the actual image, release notes for v0.1.0.
  - Acceptance: every command is copy-pasteable and was executed; internal links valid.
  - Covers: §17

- [x] **T-053** (test) Run the full quality gate suite.
  - Commands: `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src`,
    `uv run pytest --cov --cov-branch --cov-fail-under=100`, `docker build`,
    `gitleaks detect`, `bandit -r src`, `pip-audit`.
  - Acceptance: all pass; coverage report attached to the PR; no new `# pragma: no cover` beyond the
    allowed set; steady-state RSS at reference load is ≤ 256 MB and recorded.
  - Covers: NFR-101, NFR-108, §11.6

- [ ] **T-054** (test) Live end-to-end check with the dedicated test bot and chat.
  - Steps: start Compose with the test token; `/addchat` the test group; `/newpoll` a one-off two
    minutes ahead; confirm exactly one poll with the expected question/options; restart the container
    during the window and confirm no duplicate and no late send beyond grace; check `/status`,
    `/healthz`, `/metrics`.
  - Acceptance: transcript and log excerpts (sanitized) attached to the PR; exactly one delivery row
    `sent`.
  - Covers: G-2, G-3, G-4, NFR-103, NFR-104

- [x] **T-055** (docs) Reconcile code against the spec and prepare G4.
  - Files: `specs/001-core-bot/spec.md` §15–§16, PR description
  - Contents: FR/NFR/invariant → code + test mapping table, deviations with reasons, out-of-scope
    confirmation, rollback evidence, gate record draft.
  - Acceptance: every FR-1xx and I-n has at least one test; deviations are either fixed or recorded;
    G4 review requested.
  - Covers: §12.2 step 12

## Progress log

| Date | Task | Status | Notes |
|---|---|---|---|
| 2026-10-31 | T-000 | blocked | Awaiting approval to run `uv lock` / `uv sync` (network). |
