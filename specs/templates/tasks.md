# Tasks: SPEC-<NNN> — <Feature title>

Each task is independently verifiable, scoped to one day or less, and includes its tests and docs.
Order is dependency order. Check a task only when its acceptance check passes in CI.

## Conventions

- ID: `T-001`, `T-002`, …
- Type: `test`, `feat`, `fix`, `docs`, `chore`, `migration`, `ops`
- Every implementation task is preceded by its test task.
- Docs, migration, observability, and security work are tasks, not afterthoughts.

## Task list

### Phase 1 — Tests

- [ ] **T-001** (test) Write failing unit tests for …
  - Files: `tests/unit/…`
  - Acceptance: tests fail for the expected reason before implementation.
  - Covers: FR-001, I-<n>

- [ ] **T-002** (test) Write integration tests for …
  - Files: `tests/integration/…`
  - Acceptance: uses a temporary SQLite DB and fake ports; no network.
  - Covers: FR-002

### Phase 2 — Implementation

- [ ] **T-003** (feat) Implement …
  - Files: `src/telegram_auto_poll/…`
  - Acceptance: T-001/T-002 pass; no new lint/type errors.
  - Covers: FR-001

- [ ] **T-004** (migration) Add Alembic migration for …
  - Files: `src/telegram_auto_poll/db/migrations/versions/…`
  - Acceptance: upgrades an empty DB and a DB at the previous schema; backup path verified.
  - Covers: I-<n>

### Phase 3 — Operations and documentation

- [ ] **T-005** (feat) Add logs, metrics, and alerts for …
  - Files: `observability/…`, `docs/runbook.md`
  - Acceptance: metric names match the constitution; alert has a runbook entry.
  - Covers: NFR-<n>

- [ ] **T-006** (docs) Update README, `.env.example`, and spec artifacts.
  - Acceptance: commands are copy-pasteable; no broken internal links.

### Phase 4 — Verification

- [ ] **T-007** Run `ruff format --check .`, `ruff check .`, `mypy src`, `pytest --cov`.
  - Acceptance: all pass; coverage is 100% line and branch.
- [ ] **T-008** Verify the Docker image and `/healthz` in Compose.
  - Acceptance: container is healthy; `/version` matches the build.
- [ ] **T-009** Reconcile code against the spec and record deviations.
  - Acceptance: every FR/NFR and invariant mapped to code and tests; G4 evidence attached.

## Progress log

| Date | Task | Status | Notes |
|---|---|---|---|
| … | T-001 | … | … |
