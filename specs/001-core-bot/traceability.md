# SPEC-001 traceability and deviations

Status: implementation complete; **G3 requested**, G4 pending. Every requirement below maps to
production code and at least one automated test that asserts the behaviour, not merely execution.

## Functional requirements

| FR | Code | Test(s) |
|---|---|---|
| 101 configuration validation | `config.py` | `tests/unit/test_config.py` |
| 102 SQLite schema, PRAGMAs, migrations | `db/models.py`, `db/engine.py`, `db/migrations/` | `tests/integration/test_schema.py`, `test_migrate.py` |
| 103 single instance | `app.py::SingleInstanceLock` | `tests/integration/test_lock.py` |
| 104 startup recovery | `services/recovery_service.py` | `tests/integration/test_recovery.py` |
| 105 pre-migration backup + retention | `db/migrate.py` | `tests/integration/test_migrate.py` |
| 106 private-chat allowlist | `bot/auth.py` | `tests/unit/test_auth.py`, `test_handlers.py` |
| 107 callback re-authorization | `bot/auth.py`, `handlers/commands.py` | `tests/unit/test_auth.py`, `test_handlers.py` |
| 108 command rate limits | `bot/auth.py` | `tests/unit/test_auth.py` |
| 109 `/start /help /version /status` | `handlers/commands.py` | `tests/unit/test_handlers.py` |
| 110 private command menu | `bot/application.py` | `tests/unit/test_application.py` |
| 111 one wizard per admin + TTL | `wizards/store.py` | `tests/unit/test_wizard_engine.py` |
| 112 `/addchat` forward or id | `services/chat_service.py`, `handlers/commands.py` | `tests/unit/test_chat_service.py`, `test_handlers.py` |
| 113 channel posting permission | `services/chat_service.py`, `bot/telegram_gateway.py` | `tests/unit/test_chat_service.py`, `test_gateway.py` |
| 114 idempotent registration | `services/chat_service.py` | `tests/unit/test_chat_service.py` |
| 115 `/chats`, `/removechat`, skip pending | `services/chat_service.py`, `handlers/commands.py` | `tests/unit/test_chat_service.py`, `test_handlers.py` |
| 116 `/newpoll` atomic create | `wizards/engine.py`, `services/poll_service.py` | `tests/unit/test_wizard_engine.py`, `test_poll_service.py` |
| 117 invalid re-prompt with length | `wizards/engine.py` | `tests/unit/test_wizard_engine.py` |
| 118 preview with next five | `wizards/engine.py` | `tests/unit/test_wizard_engine.py` |
| 119 `/polls`, `/poll` | `services/poll_service.py`, `handlers/commands.py` | `tests/unit/test_poll_service.py`, `test_handlers.py` |
| 120 plain-text polls | `bot/telegram_sender.py`, `bot/render.py` | `tests/unit/test_sender.py`, `test_render.py` |
| 121 schedule kinds | `services/schedule_service.py`, `domain/schedule_math.py` | `tests/unit/test_schedule_service.py`, `test_schedule_math.py` |
| 122 weekday/day/once validation | `domain/validation.py`, `wizards/engine.py` | `tests/unit/test_validation.py`, `test_wizard_engine.py` |
| 123 schedule views with runs | `services/schedule_service.py`, `bot/render.py` | `tests/unit/test_schedule_service.py`, `test_render.py` |
| 124 pause/resume/delete | `services/schedule_service.py` | `tests/unit/test_schedule_service.py` |
| 125 tick creates rows and advances | `services/delivery_service.py`, `scheduler/planner.py` | `tests/unit/test_delivery_service.py`, `test_job_planner.py` |
| 126 graceful shutdown | `app.py::Runtime` | `tests/unit/test_runtime.py` |
| 127 idempotent delivery creation | `db/repositories/deliveries.py` | `tests/integration/test_schema.py`, `test_repositories.py` |
| 128 grace-window skip | `services/delivery_service.py` | `tests/unit/test_delivery_service.py` |
| 129 send payload | `services/delivery_service.py`, `bot/telegram_sender.py` | `tests/unit/test_delivery_service.py`, `test_sender.py` |
| 130 pacing and bounded concurrency | `services/rate_limiter.py`, `scheduler/worker.py` | `tests/unit/test_rate_limiter.py`, `test_worker.py` |
| 131 retries, 429, terminal alert | `services/delivery_service.py` | `tests/unit/test_delivery_service.py` |
| 132 `/sendnow` manual deliveries | `services/delivery_service.py`, `handlers/commands.py` | `tests/unit/test_delivery_service.py`, `test_handlers.py` |
| 133 terminal fields and sanitized errors | `services/delivery_service.py` | `tests/unit/test_delivery_service.py` |
| 134 audit inside the transaction | `services/audit_service.py` | `tests/unit/test_audit_service.py`, `tests/integration/test_transactions.py` |
| 135 batched retention | `services/retention_service.py` | `tests/unit/test_audit_service.py` |
| 136 health and metrics | `observability/health.py`, `metrics.py` | `tests/integration/test_health.py` |
| 137 JSON logs with redaction | `logging_setup.py` | `tests/unit/test_logging.py` |
| 138 image and Compose hardening | `Dockerfile`, `docker-compose.yml`, CI | files present; **container checks unverified** |

## Non-functional requirements

| NFR | Evidence |
|---|---|
| 101 quality gates | ruff, mypy, pytest at 100 % line+branch run locally |
| 102 single process/supervision | `app.py::Runtime`, `test_runtime.py` |
| 103/104 at-most-once, timeliness | partial unique index + claim/recovery tests |
| 105 no network in tests | `tests/conftest.py` socket guard |
| 106 `/status` latency | no timing harness yet (caveat) |
| 107 image size | **unverified** (no Docker daemon) |
| 108 RSS envelope | **unverified** (no running container) |

## Invariants

I-1/2 validators and DB checks (`test_validation.py`, `test_schema.py`); I-3 timezone validation;
I-4 weekly/monthly bounds; I-5 future one-off; I-6 date order; I-7 paused/expired `next_run` null;
I-8 monotonic next runs; I-9 partial unique index; I-10 sent rows carry message id and completion;
I-11 terminal rows carry `completed_at`; I-12 archived poll rejected; I-13/14/15 enforced by models,
PRAGMAs, and repository tests. Full matrix: `tests/unit/test_validation.py`,
`tests/integration/test_schema.py`, `tests/integration/test_repositories.py`.

## Deviations (for G4)

1. `deliveries.skip_reason` column and `SkipReason` values are additive to constitution §5.2.
2. Wizard is driven by text commands (`next`, `done`, `toggle`, …) plus an option-remove callback,
   rather than a full inline-button flow; handlers and engine are complete and tested.
3. Handler tests use structurally equivalent fake updates, not PTB `Update` objects; PTB wiring is
   covered by `test_application.py`.
4. Health handlers are called directly instead of through an aiohttp test client, keeping NFR-105.
5. SIGTERM is proven in-process (handler → stop event → bounded drain); no forked subprocess test.
6. `Dockerfile`/`docker-compose.yml`/CI are written but not executed here: Docker daemon, Compose
   plugin, and a CI run were unavailable; the live T-054 check also needs the dedicated bot token.
7. The Dockerfile pins the `python:3.12-slim` tag, not a digest; CI actions use version tags, not
   commit SHAs (both flagged by the spec, awaiting an offline-verifiable pin update).

## Live-check findings (T-054)

Two production-only defects were found by the live run and fixed before G4:

1. **Wrong chat id on send** — `deliveries.chat_id` references the `chats.id` surrogate, but
   `sendPoll` received that surrogate instead of `chats.telegram_chat_id` (“Chat not found”). Fixed in
   `services/delivery_service.py`; asserted by the payload and pacer tests.
2. **Confirmation buttons encoded no entity** — `confirmation_keyboard` produced
   `v1:w:confirm:<id>` while the handler expects `<entity>_<id>`, so `/sendnow`, `/removechat`, and
   `/delete_schedule` confirmations were rejected. Fixed in `bot/keyboards.py` and locked by a
   producer/consumer test that takes the real button data and feeds it to `handle_callback`.

Both fixes are on PR #2 with CI green.
