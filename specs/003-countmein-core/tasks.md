# Tasks: SPEC-003 — CountMeIn core

Each task is independently verifiable, scoped to one day or less, and includes its tests and docs.
Order is dependency order. Check a task only when its acceptance check passes in CI.

## Conventions

- ID: `T-001`, `T-002`, …
- Type: `test`, `feat`, `fix`, `docs`, `chore`, `migration`, `ops`
- Every implementation task is preceded by its test task.
- Docs, migration, observability, and security work are tasks, not afterthoughts.
- `Covers:` lists the spec FRs, NFRs, and invariants.

## Task list

### Phase 0 — Amendment and supersession

- [x] **T-001** (docs) Apply the constitution v2.0.0 amendment after G2.
  - Files: `CONSTITUTION.md`, `specs/003-countmein-core/constitution-amendment.md`
  - Acceptance: mission, scope, C-7, §5, §6, §7, §8, §10, §11, §16, §21, §22.4, and §22.6 match the
    approved amendment; version and ratification date updated; amendment log row added.
  - Covers: process §18.2–§18.3.

- [x] **T-002** (docs) Add ADR-0003 (product pivot) and ADR-0004 (participant callback authorization).
  - Files: `docs/adr/0003-countmein-product-pivot.md`, `docs/adr/0004-participant-callback-authorization.md`,
    `docs/adr/README.md`
  - Acceptance: statuses are `Proposed` until G2, then `Accepted`; index updated; alternatives and
    consequences recorded.
  - Covers: constitution §18.4.

- [x] **T-003** (docs) Supersede SPEC-002 and close issue #4 without merging its branch.
  - Files: `specs/002-buttons-wizard/spec.md` (status line), issue #4 comment
  - Acceptance: SPEC-002 is marked `Superseded by SPEC-003`; #4 is closed with a link to #7; the
    branch is retained until the maintainer confirms deletion.
  - Covers: roadmap §17.

- [x] **T-004** (docs) Mark the repository status as v0.3.0-alpha in the README.
  - Files: `README.md`
  - Acceptance: the README states that scheduling returns in v0.4.0 and that v0.3.0-alpha is not a
    production replacement.
  - Covers: spec §13.

### Phase 1 — Tests

- [x] **T-005** (test) Write failing unit tests for poll validation.
  - Files: `tests/unit/test_poll_validation.py`
  - Acceptance: title 0/1/300/301; options 0/1/10/11; label 1/100/101; blank/duplicate options;
    first-name 0/1/64/65; case folding. Tests fail for the expected reason.
  - Covers: FR-306, FR-307, I-16, I-17, I-27.

- [x] **T-006** (test) Write failing unit tests for the poll renderer.
  - Files: `tests/unit/test_poll_render.py`
  - Acceptance: 0/1/N/>N names; duplicate names; `+N more`; closed/deleted states; 4,096 boundary;
    long labels; deterministic ordering; no parse mode.
  - Covers: FR-326–FR-328, I-30.

- [x] **T-007** (test) Write failing unit tests for callback codec and authorization.
  - Files: `tests/unit/test_callbacks.py`, `tests/unit/test_auth.py`
  - Acceptance: `v1:v`, `v1:p`, `v1:t`, `v1:w`, `v1:c` round-trip; every key ≤64 bytes; malformed
    data rejected; admin namespaces require private chat + allowlist; vote namespace does not; group
    admin callbacks rejected.
  - Covers: FR-317, FR-318, FR-319, FR-323, ADR-0004.

- [x] **T-008** (test) Write failing unit tests for poll and publish services.
  - Files: `tests/unit/test_poll_service.py`, `tests/unit/test_publish_service.py`
  - Acceptance: create/close/reopen/delete/refresh transitions; publish creates one instance and N
    messages; at least one target required; duplicate publish creates a new instance; deleted poll
    cannot publish; audits written.
  - Covers: FR-306, FR-309–FR-314, FR-336.

- [x] **T-009** (test) Write failing unit tests for response service and vote rate limit.
  - Files: `tests/unit/test_response_service.py`
  - Acceptance: toggle on/off; multi-select; closed/deleted/unknown-option refusals; duplicate first
    names; rate limit; no audit row for votes.
  - Covers: FR-320–FR-325, FR-336.

- [x] **T-010** (test) Write failing unit tests for the response broadcaster.
  - Files: `tests/unit/test_broadcaster.py`
  - Acceptance: debounce/coalescing; dirty re-run; `not modified`; `not found`; 429; 5xx; bounded
    concurrency; shutdown flush; no transaction across an await.
  - Covers: FR-322, FR-329, FR-330, NFR-302, NFR-305.

- [x] **T-011** (test) Write failing unit tests for the posting worker.
  - Files: `tests/unit/test_posting_worker.py`
  - Acceptance: success; retry; exhaustion; non-retryable; inactive chat; closed instance; duplicate
    claim; ambiguous `sending` recovery; one alert per terminal failure.
  - Covers: FR-331–FR-335.

- [x] **T-012** (test) Write failing unit tests for retention and recovery.
  - Files: `tests/unit/test_retention.py`, `tests/unit/test_recovery.py`
  - Acceptance: open instance retained; closed/deleted pruned; redaction before deletion; idempotent;
    batched; `sending` → `failed` on startup.
  - Covers: FR-303, FR-337, I-18.

- [x] **T-013** (test) Write failing unit tests for bot commands and the new poll wizard.
  - Files: `tests/unit/test_handlers.py`, `tests/unit/test_wizard_engine.py`,
    `tests/unit/test_keyboards_poll.py`
  - Acceptance: every wizard transition, cancel, timeout, validation re-prompt; publish target
    pagination and selection; poll detail actions; responses pagination; empty states.
  - Covers: FR-306–FR-316.

- [x] **T-014** (test) Write failing unit tests for config, metrics, and health.
  - Files: `tests/unit/test_config.py`, `tests/unit/test_metrics.py`, `tests/unit/test_health.py`
  - Acceptance: every new variable and boundary; destructive-migration flag; metric names/labels;
    `/healthz` 200/503 on DB/worker heartbeat.
  - Covers: FR-301, FR-338, FR-339, NFR-301.

- [x] **T-015** (test) Write failing integration tests for repositories and migration 0002.
  - Files: `tests/integration/test_poll_repositories.py`, `tests/integration/test_migration_0002.py`
  - Acceptance: unique/FK enforcement; publish/worker/vote/broadcast flow on SQLite; migration on
    empty and 0001-seeded DBs; guard aborts with old rows; `chats`/`audit_events` survive.
  - Covers: FR-302, I-19–I-26.

### Phase 2 — Implementation

- [x] **T-016** (feat) Add domain entities, enums, and errors.
  - Files: `src/telegram_auto_poll/domain/entities.py`, `domain/enums.py`, `domain/errors.py`
  - Acceptance: T-005/T-008/T-009 compile against the new types; no I/O imports.
  - Covers: spec §7, §4.

- [x] **T-017** (feat) Implement poll validation.
  - Files: `src/telegram_auto_poll/domain/validation.py`
  - Acceptance: T-005 passes; exact constraint messages; case-folded uniqueness.
  - Covers: FR-306, FR-307, I-16, I-17, I-27.

- [x] **T-018** (feat) Implement the pure poll renderer.
  - Files: `src/telegram_auto_poll/domain/poll_render.py`
  - Acceptance: T-006 passes; ≤4,096; deterministic; plain text; closed/deleted states.
  - Covers: FR-326–FR-328.

- [x] **T-019** (feat) Add SQLAlchemy models and repositories.
  - Files: `src/telegram_auto_poll/db/models.py`, `db/repositories/polls.py`,
    `db/repositories/poll_options.py`, `db/repositories/poll_instances.py`,
    `db/repositories/poll_messages.py`, `db/repositories/poll_responses.py`, `db/uow.py`
  - Acceptance: T-015 passes; no commits in repositories; typed `Mapped[]` models; eager loading.
  - Covers: spec §7.

- [x] **T-020** (migration) Add Alembic migration `0002_countmein_core` with the destructive guard.
  - Files: `src/telegram_auto_poll/db/migrations/versions/0002_countmein_core.py`,
    `src/telegram_auto_poll/db/migrate.py`
  - Acceptance: upgrades empty and 0001 DBs; aborts when old rows exist and the flag is false; backup
    path verified; `chats`/`audit_events` preserved.
  - Covers: FR-302, I-18–I-30.

- [x] **T-021** (feat) Implement `PollService`.
  - Files: `src/telegram_auto_poll/services/poll_service.py`
  - Acceptance: T-008 passes; create/read/list/close/reopen/delete/refresh; transaction boundaries;
    audit rows.
  - Covers: FR-306, FR-308, FR-311–FR-314, FR-336.

- [x] **T-022** (feat) Implement `PublishService`.
  - Files: `src/telegram_auto_poll/services/publish_service.py`
  - Acceptance: T-008 passes; one instance + N messages in one transaction; target validation;
    confirmation data; audit.
  - Covers: FR-309, FR-310.

- [x] **T-023** (feat) Implement `ResponseService` and the vote rate limiter.
  - Files: `src/telegram_auto_poll/services/response_service.py`,
    `src/telegram_auto_poll/services/rate_limiter.py`
  - Acceptance: T-009 passes; toggle on/off; multi-select; refusals; per-user bucket; dirty marking.
  - Covers: FR-319–FR-325.

- [x] **T-024** (feat) Implement `ResponseBroadcaster`.
  - Files: `src/telegram_auto_poll/services/broadcaster.py`
  - Acceptance: T-010 passes; per-instance debounce; render once; bounded edits; retries; shutdown
    flush; no event-loop blocking.
  - Covers: FR-322, FR-329, FR-330, NFR-302, NFR-305.

- [x] **T-025** (feat) Implement `PostingWorker`.
  - Files: `src/telegram_auto_poll/services/posting_worker.py`
  - Acceptance: T-011 passes; claim/send/retry/skip; one alert per terminal failure; per-chat/global
    limits.
  - Covers: FR-331–FR-335.

- [x] **T-026** (feat) Adapt retention and recovery.
  - Files: `src/telegram_auto_poll/services/retention_service.py`,
    `src/telegram_auto_poll/services/recovery_service.py`
  - Acceptance: T-012 passes; response retention; redaction; `sending` recovery.
  - Covers: FR-303, FR-337, I-18.

- [x] **T-027** (feat) Implement bot auth split, callback codec, keyboards, and render helpers.
  - Files: `src/telegram_auto_poll/bot/auth.py`, `bot/callbacks.py`, `bot/keyboards.py`,
    `bot/render.py`
  - Acceptance: T-007 passes; admin/vote namespaces; ≤64 bytes; vote keyboard; poll detail; target
    selection; confirmations.
  - Covers: FR-317–FR-319, FR-323, ADR-0004.

- [x] **T-028** (feat) Implement the new poll wizard and commands.
  - Files: `src/telegram_auto_poll/bot/wizards/`, `bot/handlers/commands.py`,
    `bot/application.py`
  - Acceptance: T-013 passes; all commands in spec §8.1; removed commands are gone; wizard TTL and
    cancel.
  - Covers: FR-306–FR-316.

- [x] **T-029** (feat) Implement the Telegram message gateway (send/edit).
  - Files: `src/telegram_auto_poll/bot/telegram_gateway.py`,
    `src/telegram_auto_poll/bot/telegram_sender.py` (delete)
  - Acceptance: `send_message` and `edit_message_text` used; no `sendPoll` remains; error
    classification matches FR-329/FR-333.
  - Covers: FR-329, FR-331, FR-333.

- [x] **T-030** (feat) Wire the runtime, heartbeat, metrics, and health.
  - Files: `src/telegram_auto_poll/app.py`, `scheduler/engine.py`, `scheduler/heartbeat.py`,
    `observability/metrics.py`, `observability/health.py`
  - Acceptance: T-014 passes; worker/broadcaster supervised; heartbeat; `/healthz` DB + worker;
    metrics exposed.
  - Covers: FR-338, FR-339, NFR-302.

- [x] **T-031** (feat) Add the new configuration settings and `.env.example` entries.
  - Files: `src/telegram_auto_poll/config.py`, `.env.example`
  - Acceptance: T-014 passes; every variable documented with default and range; no secret in the
    example.
  - Covers: FR-301.

- [x] **T-032** (chore) Remove native-poll code and obsolete tests.
  - Files: `services/schedule_service.py`, `services/delivery_service.py`,
    `scheduler/planner.py`, `scheduler/worker.py`, `domain/schedule_math.py`,
    native-poll wizard states, and their tests
  - Acceptance: no `sendPoll`, `ScheduleRow`, `DeliveryRow`, or native poll wizard references remain;
    the suite is green; no dead code.
  - Covers: FR-305, spec §3 NG-1.

### Phase 3 — Operations and documentation

- [x] **T-033** (docs) Update README, runbook, `.env.example`, and CHANGELOG.
  - Files: `README.md`, `docs/runbook.md`, `.env.example`, `CHANGELOG.md`
  - Acceptance: quickstart works; new commands and limits documented; deletion/retention procedure;
    destructive migration and rollback procedure; alpha status.
  - Covers: FR-337, FR-341, spec §13.

- [x] **T-034** (ops) Update Docker/Compose/CI for the new health and config.
  - Files: `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`
  - Acceptance: image builds; healthcheck passes; no new dependency; CI runs the new suite and
    migration check.
  - Covers: FR-341, NFR-311.

### Phase 4 — Verification

- [x] **T-035** (test) Run the full local suite and coverage gate.
  - Files: none
  - Acceptance: `ruff format --check .`, `ruff check .`, `mypy src`,
    `pytest --cov --cov-branch --cov-fail-under=100` all pass; no network/sleeps.
  - Covers: NFR-301, NFR-304.

- [x] **T-036** (ops) Verify the Docker image and Compose healthcheck.
  - Files: none
  - Acceptance: `docker build` succeeds; container healthy; `/version` matches build args; a staging
    poll can be created, published, voted, closed, and deleted.
  - Covers: FR-341, NFR-310.

- [x] **T-037** (review) Request G3 code review.
  - Files: PR diff, `specs/003-countmein-core/traceability.md`
  - Acceptance: a non-author reviewer approves; comments resolved; no self-approval.
  - Covers: workflow §12.2 step 10.

- [x] **T-038** (ops) Run governance checks.
  - Files: CI security jobs
  - Acceptance: gitleaks, bandit, pip-audit, CodeQL, Trivy green or risk-accepted with an expiry.
  - Covers: constitution §11.6.

- [ ] **T-039** (review) Reconcile code against the spec and request G4.
  - Files: `specs/003-countmein-core/traceability.md`
  - Acceptance: every FR/NFR and invariant mapped to code and tests; deviations documented; G4
    approval recorded.
  - Covers: workflow §12.2 step 12.

- [ ] **T-040** (ops) Open the PR, monitor CI, address comments, and merge only when authorized.
  - Files: PR
  - Acceptance: PR links issue #7 and the spec; CI green; branch deleted; issue updated.
  - Covers: workflow §12.2 steps 15–17.

## Progress log

| Date | Task | Status | Notes |
|---|---|---|---|
| 2026-11-02 | T-001–T-004 | Complete | Constitution v2.0.0, ADR-0003/0004, SPEC-002 superseded, README alpha status. |
| 2026-11-02 | T-005–T-032 | Complete | Implementation and tests; 333 tests at 100% line and branch. |
| 2026-11-02 | T-033–T-038 | Complete | Docs, Docker/CI, G3 approved, governance checks and risk acceptance. |
| 2026-11-02 | T-039 | Pending | G4 reconciliation requested. |
| 2026-11-02 | T-040 | Pending | PR after G4. |
