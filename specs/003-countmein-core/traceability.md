# Traceability: SPEC-003 — CountMeIn core

| Field | Value |
|---|---|
| Spec | [`spec.md`](spec.md) |
| Plan | [`plan.md`](plan.md) |
| Tasks | [`tasks.md`](tasks.md) |
| Status | Reconciled — G4 approved 2026-11-02 |

## Functional requirements

| Requirement | Implementation | Tests |
|---|---|---|
| FR-301 configuration | `config.py` | `tests/unit/test_config.py` |
| FR-302 migration + destructive guard | `db/migrations/versions/0002_countmein_core.py`, `db/migrate.py` | `tests/integration/test_migrate.py` |
| FR-303 startup recovery | `services/recovery_service.py`, `services/posting_worker.py` | `tests/unit/test_edge_cases.py`, `tests/unit/test_posting_worker.py` |
| FR-304 graceful shutdown | `app.py` (`Runtime`) | `tests/unit/test_build_runtime.py` |
| FR-305 posting worker replaces `sendPoll` | `services/posting_worker.py`, `bot/telegram_gateway.py` | `tests/unit/test_posting_worker.py`, `tests/unit/test_conventions.py` |
| FR-306 poll wizard | `bot/wizards/engine.py`, `bot/wizards/states.py`, `bot/handlers/commands.py` | `tests/unit/test_wizard_engine.py`, `tests/unit/test_handlers.py` |
| FR-307 validation re-prompt | `domain/validation.py`, `bot/wizards/engine.py` | `tests/unit/test_validation.py`, `tests/unit/test_wizard_engine.py` |
| FR-308 poll list/detail | `services/poll_service.py`, `bot/render.py` | `tests/unit/test_poll_service.py`, `tests/unit/test_render.py` |
| FR-309 publish | `services/publish_service.py`, `bot/handlers/commands.py` | `tests/unit/test_publish_service.py`, `tests/unit/test_handlers.py` |
| FR-310 each publish is a new instance | `services/publish_service.py` | `tests/unit/test_publish_service.py` |
| FR-311 close | `services/poll_service.py` | `tests/unit/test_poll_service.py` |
| FR-312 reopen | `services/poll_service.py` | `tests/unit/test_poll_service.py` |
| FR-313 delete + redact | `services/poll_service.py`, `services/broadcaster.py` | `tests/unit/test_poll_service.py`, `tests/unit/test_broadcaster.py` |
| FR-314 refresh | `services/poll_service.py`, `services/posting_worker.py` | `tests/unit/test_poll_service.py`, `tests/unit/test_handlers.py` |
| FR-315 response list | `services/poll_service.py`, `bot/render.py` | `tests/unit/test_poll_service.py`, `tests/unit/test_render.py` |
| FR-316 cancel | `bot/handlers/commands.py` | `tests/unit/test_handlers.py` |
| FR-317 callback codec | `domain/callbacks.py`, `bot/keyboards.py` | `tests/unit/test_callbacks.py`, `tests/unit/test_keyboards.py` |
| FR-318 participant vote | `bot/auth.py`, `services/response_service.py` | `tests/unit/test_auth.py`, `tests/unit/test_response_service.py` |
| FR-319 stored-message validation | `services/response_service.py` | `tests/unit/test_response_service.py` |
| FR-320 toggle / multi-select | `services/response_service.py`, `db/repositories/poll_responses.py` | `tests/unit/test_response_service.py`, `tests/integration/test_repositories.py` |
| FR-321 callback answer | `bot/handlers/commands.py` | `tests/unit/test_handlers.py` |
| FR-322 live coalesced edits | `services/broadcaster.py` | `tests/unit/test_broadcaster.py` |
| FR-323 vote rate limit | `bot/auth.py` | `tests/unit/test_auth.py`, `tests/unit/test_handlers.py` |
| FR-324 first-name snapshots | `domain/validation.py`, `services/response_service.py` | `tests/unit/test_validation.py`, `tests/unit/test_response_service.py` |
| FR-325 refusals | `services/response_service.py` | `tests/unit/test_response_service.py` |
| FR-326 rendering | `domain/poll_render.py` | `tests/unit/test_poll_render.py` |
| FR-327 4,096 limit | `domain/poll_render.py` | `tests/unit/test_poll_render.py` |
| FR-328 closed/deleted rendering | `domain/poll_render.py` | `tests/unit/test_poll_render.py` |
| FR-329 edit error handling | `services/broadcaster.py`, `bot/telegram_gateway.py` | `tests/unit/test_broadcaster.py`, `tests/unit/test_gateway.py` |
| FR-330 bounded edit concurrency | `services/broadcaster.py` | `tests/unit/test_broadcaster.py` |
| FR-331 send pending | `services/posting_worker.py` | `tests/unit/test_posting_worker.py` |
| FR-332 send pacing | `services/posting_worker.py`, `services/rate_limiter.py` | `tests/unit/test_posting_worker.py`, `tests/unit/test_rate_limiter.py` |
| FR-333 retries/alerts | `services/posting_worker.py` | `tests/unit/test_posting_worker.py` |
| FR-334 skip inactive/closed | `services/posting_worker.py` | `tests/unit/test_posting_worker.py` |
| FR-335 at-most-once | `db/models.py`, `db/repositories/poll_messages.py` | `tests/integration/test_repositories.py` |
| FR-336 audit | `services/poll_service.py`, `services/chat_service.py`, `db/repositories/audit.py` | `tests/unit/test_poll_service.py`, `tests/unit/test_chat_service.py`, `tests/unit/test_audit_service.py` |
| FR-337 retention | `services/retention_service.py` | `tests/unit/test_retention.py`, `tests/unit/test_edge_cases.py` |
| FR-338 status | `services/status_service.py`, `bot/render.py` | `tests/unit/test_status_service.py`, `tests/unit/test_render.py` |
| FR-339 health | `observability/health.py`, `scheduler/engine.py` | `tests/unit/test_health.py`, `tests/unit/test_scheduler_engine.py` |
| FR-340 logs | `logging_setup.py`, service log calls | `tests/unit/test_logging.py`; names/user IDs are not logged by design |
| FR-341 Docker/Compose | `Dockerfile`, `docker-compose.yml` | `tests/unit/test_build_runtime.py`; container build is a manual/CI check |

## Non-functional requirements

| Requirement | Evidence |
|---|---|
| NFR-301 coverage | `pytest --cov --cov-branch --cov-fail-under=100` → 100% |
| NFR-302 vote-to-edit latency | `ResponseBroadcaster` debounce + `tests/unit/test_broadcaster.py`; `tap_vote_to_edit_seconds` |
| NFR-303 duplicate posts | Partial unique index on `(instance_id, chat_id)`; `tests/integration/test_repositories.py` |
| NFR-304 test isolation | `tests/conftest.py` blocks sockets; no sleeps in the suite |
| NFR-305 edit throughput | Bounded semaphore and coalescing; `tests/unit/test_broadcaster.py` |
| NFR-306 privacy | Minimal fields, no names in logs/metrics, deletion and retention tests |
| NFR-307 admin latency | Read paths use indexed queries; not separately benchmarked in this environment |
| NFR-308 capacity | Schema/index review; no synthetic load run in this environment |
| NFR-309 resource envelope | Not measured in this environment; manual operator check |
| NFR-310 startup | `tests/unit/test_build_runtime.py` runs migrations + recovery |
| NFR-311 no new dependency | `pyproject.toml` and `uv.lock` unchanged |
| NFR-312 rollback | Runbook §3, §20; restore-from-backup path |

## Invariants

I-16–I-30 are implemented in `domain/validation.py`, `db/models.py`, the repositories, and the
services; `tests/unit/test_validation.py`, `tests/unit/test_poll_render.py`,
`tests/integration/test_repositories.py`, and `tests/unit/test_response_service.py` assert them.

## Deviations and deferred work

- **Scheduling** is deliberately not in SPEC-003. SPEC-004 restores it; v0.3.0-alpha is an integration
  milestone and must not replace production scheduling.
- **Inline mode and poll editing** are SPEC-005.
- **NFR-307/308/309** are not benchmarked in this environment; they remain operator checks.
- **SPEC-002** is superseded by issue #7. Its branch is not merged; its button components informed the
  new wizard and target selector.

## Governance evidence (Step 11)

| Check | Result |
|---|---|
| `ruff format --check .` | Pass |
| `ruff check .` | Pass |
| `mypy src` | Pass (`strict`) |
| `pytest --cov --cov-branch --cov-fail-under=100` | 333 passed, 100% line and branch |
| `bandit -r src` | No issues |
| `pip-audit` | No known vulnerabilities; the private local package is skipped (not on PyPI) |
| `gitleaks detect --no-git` | No leaks (`.gitleaks.toml` allowlists local `.env` and caches) |
| `gitleaks detect` (full history) | No leaks (34 commits) |
| `docker build` | Pass |
| Container health | `/healthz` 200 (`database: true`, `worker: true`) |
| Trivy image scan | 45 HIGH, 0 CRITICAL, 0 fixable; **risk-accepted until 2026-12-31** — see [`docs/security/risk-acceptance-2026-11-02.md`](../../docs/security/risk-acceptance-2026-11-02.md), follow-up #8 |
| CodeQL | CI-only; not run locally |

