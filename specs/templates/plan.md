# Plan: SPEC-<NNN> — <Feature title>

| Field | Value |
|---|---|
| Spec | [`spec.md`](spec.md) |
| Status | Draft / Approved |
| Author | @<author> |
| ADRs | ADR-<NNNN> (if any) |

## 1. Approach

Describe the chosen design in enough detail that another engineer could implement it. Include the
control flow, transaction boundaries, and which layer owns each responsibility.

## 2. Architecture impact

- Modules/packages added or changed:
- Dependency direction (`bot → services → domain/db`):
- New ports/adapters:
- Background tasks and their supervision:
- Configuration changes:

## 3. Data design

- Schema changes and indexes:
- Migration steps (expand/contract if needed):
- Backfill plan and idempotency:
- Query patterns and performance:
- Retention/privacy impact:

## 4. State and concurrency

- State machines touched:
- Atomic operations and uniqueness constraints:
- Crash/restart behavior:
- Rate limiting and backpressure:

## 5. Alternatives considered

| Alternative | Pros | Cons | Why rejected |
|---|---|---|---|
| … | … | … | … |

## 6. Test strategy

- Unit boundaries:
- Integration fixtures:
- Fakes (clock, sender, notifier):
- Required DST/edge-case matrix:
- Coverage plan for 100% line and branch:

## 7. Observability plan

- Logs:
- Metrics:
- Health:
- Alerts:

## 8. Security plan

- Authorization points:
- Validation points:
- Secret handling:
- Abuse/rate-limit controls:

## 9. Rollout and rollback

- Deployment order:
- Feature flags (if any):
- Migration backup:
- Rollback procedure:
- Monitoring after deploy:

## 10. Task sequencing

High-level phases; the authoritative list is in [`tasks.md`](tasks.md).

1. …
2. …
3. …

## 11. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| … | … | … | … |

## 12. Open questions

None at G2, or list them with an owner. Unresolved questions block G2.
