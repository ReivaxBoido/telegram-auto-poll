# Plan: SPEC-001 — Core bot, scheduled poll delivery

| Field | Value |
|---|---|
| Spec | `spec.md` (SPEC-001, issue #1) |
| Status | Approved — G2 passed 2026-10-31; implementation not started |
| Author | @ReivaxBoido |
| ADRs | ADR-0001 (stack), ADR-0002 (Telegram-only admin) |

## 1. Approach

Build the constitutional architecture as one asyncio process with four supervised components, in the
dependency order `domain → db/services → adapters → composition root`. Telegram, the clock, the
sleeper, and the HTTP client are behind `typing.Protocol` ports so every domain and service test runs
without network or real time.

Implementation order:

1. **Skeleton and contracts** — `pyproject.toml`, tooling config, package layout, ports, domain
   entities, errors, and pure schedule math. Schedule math is the highest-risk logic; it is written
   test-first from the DST/month matrix.
2. **Persistence** — SQLAlchemy 2.0 models, engine PRAGMAs, the initial Alembic migration, and
   repositories. The uniqueness constraint is the delivery guarantee, so it is proven by an integration
   test before any sender exists.
3. **Services** — chat, poll, schedule, delivery, audit, retention. Services own transactions and
   audit events; repositories only build statements.
4. **Runtime** — scheduler planning, delivery worker, startup recovery, graceful shutdown, and the
   single-instance lock.
5. **Adapters and UX** — PTB sender/notifier adapters, auth filter, callback codec, a pure wizard
   engine with an in-memory store, thin PTB handlers, and dispatcher wiring.
6. **Operations** — aiohttp health/metrics, JSON logging, Docker image, Compose, CI, and the G4
   reconciliation.

The wizard is deliberately **not** implemented with PTB `ConversationHandler`. A pure `WizardEngine`
(state + event → new state + effects) is easier to exhaustively test at 100% branch coverage, avoids
hidden state inside PTB, and keeps handler code to a few lines each.

## 2. Architecture impact

### 2.1 Modules added

```text
src/telegram_auto_poll/
├── __main__.py                 # asyncio.run(main())
├── app.py                      # composition root, lifecycle, signals
├── config.py                   # pydantic-settings Settings + validation
├── clock.py                    # Clock and Sleeper ports + system implementations
├── logging_setup.py            # JSON formatter, redaction, configuration
├── domain/
│   ├── entities.py             # frozen dataclasses: Chat, Poll, Schedule, Delivery, AuditEvent
│   ├── enums.py                # ScheduleKind, DeliveryStatus, Trigger, ChatType, AuditAction
│   ├── errors.py               # DomainError hierarchy, DeliveryError(retryable)
│   ├── validation.py           # poll/chat/schedule validators (pure)
│   ├── schedule_math.py        # next_occurrence, occurrences, DST/month logic (pure)
│   └── ports.py                # Clock, Sleeper, PollSender, ChatGateway, AdminNotifier, repositories
├── db/
│   ├── engine.py               # async engine, PRAGMAs, session factory, health SELECT 1
│   ├── models.py               # DeclarativeBase + Mapped[] models + naming convention
│   ├── repositories/           # chat, poll, schedule, delivery, audit
│   ├── migrate.py              # pre-migration backup + alembic upgrade head
│   └── migrations/             # alembic.ini env.py + versions/0001_initial.py
├── services/
│   ├── chat_service.py
│   ├── poll_service.py
│   ├── schedule_service.py
│   ├── delivery_service.py     # creation, claim, send, retry, skip
│   ├── rate_limiter.py         # per-chat, per-group, global pacing
│   ├── audit_service.py
│   ├── retention_service.py
│   └── admin_notifier.py       # port implementation helper (retry-free, logged)
├── scheduler/
│   ├── engine.py               # APScheduler wiring, job planner, heartbeat
│   └── worker.py               # delivery worker task group
├── bot/
│   ├── application.py          # PTB Application builder + dispatcher
│   ├── auth.py                 # AdminFilter, private-chat guard, command rate limit
│   ├── callbacks.py            # v1 codec + pagination
│   ├── keyboards.py            # inline keyboard builders
│   ├── render.py               # message text builders (pure)
│   ├── wizards/
│   │   ├── states.py           # WizardKind, WizardStep, WizardSession
│   │   ├── engine.py           # pure transition function
│   │   └── store.py            # in-memory store + TTL janitor
│   └── handlers/               # one module per command group
└── observability/
    ├── health.py               # aiohttp app: /livez, /healthz, /metrics
    ├── metrics.py              # prometheus registry + helpers
    └── logging.py              # structured logger adapter (re-export)
```

### 2.2 Dependency direction

- `domain/` imports only the standard library.
- `services/` imports `domain/` and repository protocols; it never imports `bot/` or `scheduler/`.
- `db/` imports `domain/` for entity mapping helpers only; no business policy.
- `bot/` and `scheduler/` are adapters that depend on services.
- `app.py` is the only place that constructs concrete infrastructure.
- A `ruff` `flake8-tidy-imports`/`import-linter` rule (or a test that walks imports) enforces that
  `domain/` does not import SQLAlchemy or Telegram.

### 2.3 Ports

| Port | Methods | Implementations |
|---|---|---|
| `Clock` | `now() -> datetime` (aware UTC) | `SystemClock`, `FakeClock` |
| `Sleeper` | `async sleep(seconds: float) -> None` | `AsyncioSleeper`, `FakeSleeper` |
| `PollSender` | `async send_poll(request: PollRequest) -> int` | `TelegramPollSender`, `FakeSender` |
| `ChatGateway` | `async get_chat(id)`, `async get_member(chat_id, user_id)` | `TelegramChatGateway`, `FakeChatGateway` |
| `AdminNotifier` | `async notify_admins(text: str) -> None` | `TelegramAdminNotifier`, `FakeNotifier` |
| `ChatRepository` | `upsert`, `get`, `list`, `set_active` | SQLAlchemy |
| `PollRepository` | `add`, `get`, `list`, `set_targets` | SQLAlchemy |
| `ScheduleRepository` | `add`, `get`, `list`, `due_before`, `update_next_run`, `set_paused`, `delete` | SQLAlchemy |
| `DeliveryRepository` | `insert_many_ignore`, `claim_due`, `mark_sent`, `mark_retry`, `mark_failed`, `mark_skipped`, `counts` | SQLAlchemy |
| `AuditRepository` | `add`, `list_recent` | SQLAlchemy |

### 2.4 Background tasks

`app.py` supervises with `asyncio.TaskGroup`:

| Task | Owner | Cancellation |
|---|---|---|
| PTB polling | `bot/application.py` | `application.stop()` in shutdown |
| APScheduler | `scheduler/engine.py` | `scheduler.shutdown(wait=False)` |
| Delivery worker pool (`SEND_CONCURRENCY` tasks) | `scheduler/worker.py` | cancel + drain ≤ `SHUTDOWN_GRACE_SECONDS` |
| Health server | `observability/health.py` | `runner.cleanup()` |
| Wizard janitor | `bot/wizards/store.py` | task cancel |

## 3. Data design

### 3.1 Schema

One migration creates all six tables (`chats`, `polls`, `poll_targets`, `schedules`, `deliveries`,
`audit_events`) with the columns and invariants from constitution §5.2.

Key DDL decisions:

- **Timestamps:** `INTEGER` Unix epoch seconds, from the injected `Clock`.
- **Booleans:** `INTEGER` with `CHECK (col IN (0,1))`.
- **Options:** `options_json TEXT NOT NULL CHECK (json_valid(options_json))`; array length and
  uniqueness are enforced in domain code plus a `CHECK (json_array_length(options_json) BETWEEN 2 AND 12)`
  where the SQLite build supports it (the official Python 3.12 `sqlite3` does).
- **Partial unique index:**
  `CREATE UNIQUE INDEX ux_delivery_occurrence ON deliveries (schedule_id, scheduled_for_utc, chat_id) WHERE schedule_id IS NOT NULL`.
  Alembic: `op.create_index(..., unique=True, sqlite_where=sa.text("schedule_id IS NOT NULL"))`.
- **Hot-path indexes:** `ix_schedules_due (is_paused, next_run_at_utc)`,
  `ix_deliveries_pending (status, next_attempt_at_utc)`, `ix_audit_created (created_at)`.
- **Naming convention** on `MetaData` so batch-mode `ALTER TABLE` produces deterministic constraint
  names (`ck_`, `fk_`, `uq_`, `ix_`, `pk_`).
- **Foreign keys:** `ON DELETE RESTRICT` for definitions, `ON DELETE SET NULL` for
  `deliveries.schedule_id` (manual sends have no schedule).

### 3.2 Migration and backup

`db/migrate.py::upgrade()`:

1. If the database file exists and `alembic_version` is behind head, take `sqlite3.Connection.backup()`
   to `/data/backups/pre-migrate-<epoch>.db` with `0600`.
2. Run `alembic upgrade head` programmatically with the async engine.
3. After success, keep the five newest `pre-migrate-*.db` and delete older ones.

The migration is tested twice: on an empty database, and on a database created at head then re-upgraded
(a no-op). A previous-version fixture is not available for the first release; the test asserts
idempotency instead.

### 3.3 Query patterns

- Due schedules: `SELECT ... WHERE is_paused = 0 AND next_run_at_utc <= :now ORDER BY next_run_at_utc LIMIT 50`.
- Pending deliveries: `SELECT ... WHERE status='pending' AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc <= :now) ORDER BY next_attempt_at_utc LIMIT :n`.
- Claim: `UPDATE deliveries SET status='sending', started_at=:now WHERE id=:id AND status='pending'`;
  the worker proceeds only when `rowcount == 1`.
- Counts for `/status`: five indexed `COUNT(*)` queries, cached for 5 s to keep p95 ≤ 2 s.

No network call is ever made while a transaction is open: the worker commits the `sending` transition,
releases the session, performs the HTTP call, then opens a new session to record the outcome.

### 3.4 Retention

`RetentionService.run()` deletes in batches of 1,000:

```sql
DELETE FROM deliveries WHERE id IN (SELECT id FROM deliveries WHERE created_at < :cutoff LIMIT 1000)
```

repeated until `rowcount == 0`, then the same for `audit_events`, then
`PRAGMA wal_checkpoint(TRUNCATE)`. Registered as an APScheduler cron job at 03:15 UTC.

## 4. State and concurrency

- **Single writer:** one process, one engine, one event loop. The `flock` on `/data/app.lock` is taken
  before the engine opens and released on exit; `LOCK_EX | LOCK_NB` failure exits with code 3.
- **Delivery claim:** optimistic `UPDATE ... WHERE status='pending'`; only one worker can win because
  the update is atomic. Concurrency is bounded by `SEND_CONCURRENCY` semaphore.
- **Idempotent creation:** `INSERT ... ON CONFLICT DO NOTHING` on the partial unique index; the code
  never treats a conflict as an error.
- **Scheduler reconciliation:** `ScheduleJobPlanner.reconcile()` reads active schedules, adds
  `DateTrigger` jobs for missing `(schedule_id, next_run_at_utc)` pairs, removes jobs whose schedule is
  paused/expired/deleted or whose run time changed, and is called at startup, after every schedule
  mutation, and after every tick. A separate interval job (`SCHEDULER_TICK_SECONDS`) is the safety net
  if a `DateTrigger` job is missed.
- **Heartbeat:** every tick writes `time.monotonic()` to an in-memory `SchedulerHeartbeat`; `/healthz`
  reads it. Monotonic time avoids clock-jump false positives.
- **Rate limiting:** `ChatRateLimiter` keeps `last_sent[chat_id]` (monotonic) and a per-minute deque for
  groups, plus a global `asyncio.Semaphore(25)` and a 1/25 s global interval. Waiting uses the
  `Sleeper` port so tests record waits instead of sleeping.
- **Grace window:** computed once per occurrence against `Clock.now()`; `late <= grace` sends,
  `late > grace` inserts `skipped`. Boundary is `<=` and is unit-tested at exactly the boundary.

## 5. Alternatives considered

| Decision | Alternative | Pros of alternative | Cons | Why rejected |
|---|---|---|---|---|
| Custom `WizardEngine` | PTB `ConversationHandler` | Less code, built-in persistence hooks | State hidden in PTB; hard to reach 100% branch coverage; per-user concurrency quirks | The engine is pure and exhaustively testable; handlers stay thin. |
| Per-schedule `DateTrigger` jobs + safety tick | Single interval tick only (15 s) | Simpler; no job churn | Latency floor equals tick; constitution §3.4 mandates next-run jobs | Keep both: jobs for precision, tick for safety. |
| JSON `options_json` | `poll_options` table | Queryable, normalized | Extra joins, ordering column, more code | Options are always read as a whole with the poll; JSON matches access pattern. |
| Optimistic claim | `SELECT ... FOR UPDATE` / advisory lock | Stronger in multi-writer | SQLite has no row locks; multi-writer is forbidden | Single writer + atomic UPDATE is sufficient and testable. |
| `aiohttp` sidecar | `uvicorn`/FastAPI | Batteries included | Adds ASGI stack for two endpoints | Constitution §1.3 and ADR-0001. |
| One transaction per send | Transaction held across HTTP | Simpler code | Long write transaction blocks retention and scheduling | Constitution §9.1 forbids network calls inside a transaction. |
| `pytest-asyncio` auto mode | Manual `@pytest.mark.asyncio` | Explicit | Boilerplate on every test | Auto mode is already mandated in §4.3. |

## 6. Test strategy

### 6.1 Fakes and fixtures

- `FakeClock(now)`: `advance(seconds)`, `set(datetime)`; asserts aware UTC.
- `FakeSleeper`: records `(seconds)` and returns immediately; `advance_clock=True` can couple it to
  `FakeClock` for rate-limiter tests.
- `FakeSender`: records `PollRequest`s; `fail_next(error)` queue; returns incrementing message IDs.
- `FakeChatGateway`: registry of fake chats and members; configurable permission failures.
- `FakeNotifier`: records admin messages.
- `tests/factories.py`: `make_poll()`, `make_chat()`, `make_schedule()`, `make_delivery()` with valid
  defaults and overrides.
- `tests/conftest.py`: session-scoped temporary directory; per-test database file with migrations
  applied once per session and rolled back by truncation; a `block_sockets` autouse fixture that
  monkeypatches `socket.socket` to raise, so accidental network use fails loudly.

### 6.2 Unit boundaries

- `domain/schedule_math.py`: pure functions, exercised with a fixed instant and IANA zones. The DST
  tests use `Europe/London` (spring-forward 01:00→02:00 in 2026) and `America/New_York` (fall-back
  02:00→01:00). A `Australia/Lord_Howe` half-hour case is a robustness test.
- `domain/validation.py`: boundary tables (0/1/max/max+1, 1/2/12/13 options, duplicate/blank).
- `services/*`: repositories are faked with in-memory implementations; services are tested for
  transaction intent (commit/rollback) via a `FakeUnitOfWork` that records calls.
- `bot/wizards/engine.py`: table-driven transitions for every `(state, event)` pair; an exhaustive test
  asserts every enum combination is handled or explicitly invalid.
- `bot/callbacks.py`: property-style test over all registered namespaces asserting encoded length ≤ 64.

### 6.3 Integration boundaries

- Repositories against a real temporary SQLite file with migrations: constraint enforcement, unique
  index behavior, FK actions, JSON checks.
- `DeliveryService` + `SchedulerEngine` + `DeliveryWorker` together with `FakeClock`, `FakeSleeper`,
  `FakeSender`; asserts row states and sender calls, never sleeps.
- `health.py` via `aiohttp.test_utils.TestClient`: 200/503 paths, metrics text contains the documented
  metric names.
- `migrate.py` with a monkeypatched Alembic command to assert backup order and retention.
- `flock` test: spawn a subprocess that attempts the lock and exits non-zero.

### 6.4 Coverage plan

- 100% line and branch is reached by keeping adapters thin and testing handlers by direct invocation
  with real `telegram.Update`/`Message` objects and a `SimpleNamespace` context.
- The only exclusions are constitution §11.3: migration versions, `__main__` guard/bootstrap,
  `TYPE_CHECKING`, and `Protocol` bodies. `# pragma: no cover` is limited to two occurrences per PR.
- CI enforces `--cov-fail-under=100`; the PR records the report.

### 6.5 Telegram handler testing

Handlers are tested without PTB's dispatcher:

```python
update = make_update(text="/status", user_id=ADMIN_ID, chat_type=ChatType.PRIVATE)
context = SimpleNamespace(bot=FakeBot(), user_data={}, chat_data={})
await status_handler(update, context)
assert fake_bot.sent[-1].text.startswith("telegram-auto-poll")
```

`FakeBot` records `send_message`, `send_poll`, `answer_callback_query`, and `get_chat` calls. This
covers the adapter mapping without a network.

## 7. Observability plan

- **Logs:** a `JsonFormatter` emits the §16.1 fields; `RedactionFilter` replaces the token and any
  `Authorization` value with `[REDACTED]`. Correlation fields are passed via `extra`. Tests capture
  with `caplog` and assert both JSON parseability and absence of a known fake token.
- **Metrics:** one module-level `CollectorRegistry`; helpers `inc`, `observe`, `set_gauge`. Gauges that
  reflect DB state (`schedules_active`, `deliveries_pending`) are refreshed on each scheduler tick so
  `/metrics` never runs a query.
- **Health:** `HealthState` holds `db_ok`, `last_heartbeat_monotonic`, `polling_ok`. `/healthz`
  evaluates DB (`SELECT 1` with a 1 s timeout) and heartbeat (< 3 ticks). Telegram polling failure is a
  metric and a component field, not a readiness failure.
- **Alerts:** terminal delivery failures call `AdminNotifier` once per delivery row (guarded by
  `completed_at` being set in the same transaction that triggers the notification).

## 8. Security plan

| Control | Implementation | Test |
|---|---|---|
| Admin authorization | `AdminFilter` on every handler; callbacks re-check `from_user.id` | `test_auth.py` |
| Private-only | `filters.ChatType.PRIVATE` + explicit guard | group command ignored |
| Input validation | domain validators, length/range/uniqueness | boundary tables |
| No HTML | `parse_mode=None` everywhere | `<b>` literal test |
| Secret redaction | formatter filter + sanitizer for `last_error` | fake-token absence test |
| SQL injection | SQLAlchemy constructs only; no `text()` with interpolation | review + `grep` gate |
| Callback forgery | version + namespace + actor check; state not trusted from callback data | forged callback test |
| Container | non-root, read-only FS, `cap_drop: ALL`, `no-new-privileges` | Dockerfile review + Trivy |
| Rate limits | admin bucket + sender buckets | fake-clock tests |

## 9. Rollout and rollback

1. Merge the PR, tag `v0.1.0`; CI publishes `ghcr.io/reivaxboido/telegram-auto-poll:v0.1.0` with SBOM.
2. Operator copies `.env` (token, admin IDs, `DEFAULT_TIMEZONE`), runs `docker compose pull && up -d`.
3. Verify `/healthz`, `/version`, `/status`; register a test chat; `/sendnow` a test poll.
4. **Rollback:** redeploy the previous image. In the first release there is no previous image, so
   rollback means stopping the container and restoring `/data/backups/pre-migrate-*.db` if a migration
   ran; no data loss on a clean start.
5. **Feature flag:** none. The risk is contained by deploying with only a staging chat registered, then
   adding production chats after the smoke test.

## 10. Task sequencing

Phases match `tasks.md`:

1. **Scaffolding and contracts** (T-001–T-010): tooling, package, config, domain, schedule math, clock.
2. **Persistence** (T-011–T-017): models, migration, repositories, startup migration, lock.
3. **Services** (T-018–T-031): chat, poll, schedule, delivery, rate limiter, sender adapter, audit,
   retention.
4. **Runtime** (T-032–T-037): planner, scheduler, worker, recovery, shutdown.
5. **Bot UX** (T-038–T-046): auth, callbacks, wizard engine, handlers, dispatcher.
6. **Observability** (T-047–T-048): health, metrics, logging.
7. **Packaging and verification** (T-049–T-055): Docker, Compose, CI, docs, full gate run, live check,
   G4 reconciliation.

## 11. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| DST/month math bug ships | Medium | High | Pure functions, exhaustive matrix, preview in the wizard, 100% branch coverage on `schedule_math`. |
| PTB handler coverage is hard to reach | Medium | Medium | Thin handlers, direct invocation with real `Update` objects, `FakeBot`; no dispatcher in tests. |
| APScheduler job drift after mutations | Medium | Medium | DB is authoritative; reconcile after every mutation and every tick; integration test for add/remove/re-time. |
| Long transaction blocks the writer | Low | Medium | No network inside a transaction; short sessions; `busy_timeout`; retention batched. |
| Flaky time-based tests | High if careless | Medium | `FakeClock` + `FakeSleeper`; the socket-blocking fixture; no `sleep` in tests. |
| 100% coverage encourages assertion theater | Medium | Medium | Reviewers check behavior assertions; mutation-style spot checks on schedule math and retry logic. |
| `uv lock` requires network access | Certain | Low | Ask for human approval before the first `uv lock`/`uv sync`; it is a prerequisite task, not a hidden action. |
| Image size or build breakage | Medium | Low | Multi-stage build, slim base, CI size check at 250 MB. |
| First migration cannot be rolled back | Low | Medium | Pre-migration backup, empty-DB test, and no destructive changes after v0.1.0. |

## 12. Open questions

None. G1 is closed on issue #1; the remaining prerequisite is human approval to run `uv lock`/`uv sync`
(network access) when implementation starts, per the repository's no-download rule.
