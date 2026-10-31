# SPEC-<NNN>: <Feature title>

| Field | Value |
|---|---|
| Spec ID | SPEC-<NNN> |
| Status | Draft / Clarified / Approved / Implementing / Review / Reconciled / Delivered |
| Owner | @<author> |
| Issue | #<number> |
| Plan | [`plan.md`](plan.md) |
| Tasks | [`tasks.md`](tasks.md) |
| Target release | v<X.Y.Z> (or TBD) |
| Delivered in | v<X.Y.Z> / PR #<number> (fill after merge) |

## 1. Problem

What user problem exists today? What is the cost of not solving it? Link relevant issues and prior
decisions.

## 2. Goals

- G-1: <observable outcome>
- G-2: <observable outcome>

## 3. Non-goals

- NG-1: <explicitly excluded behavior and why>
- NG-2: <deferred behavior and trigger>

## 4. Users and stories

| Persona | Story |
|---|---|
| Admin | As an admin, I want … so that … |
| Operator | As an operator, I want … so that … |

## 5. Functional requirements

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-001 | The bot MUST … | P0 | Given …, when …, then … |
| FR-002 | The bot MUST … | P1 | Given …, when …, then … |

Add as many rows as needed. P0 = release blocker, P1 = required, P2 = should-have.

## 6. Non-functional requirements

| ID | Requirement | Target | Verification |
|---|---|---|---|
| NFR-001 | Latency | … | … |
| NFR-002 | Security | … | … |

## 7. Data model changes

- Tables/columns/indexes added or changed:
- Migration strategy and backward compatibility:
- Retention impact:
- Invariants affected (`I-*` from the constitution):

## 8. Telegram UX

- Commands added/changed:
- Wizard steps:
- Inline keyboards and callback data (≤ 64 bytes):
- Empty, error, and confirmation states:
- Message limits respected:

## 9. Edge cases and failure modes

| Case | Expected behavior | Test |
|---|---|---|
| Timezone/DST boundary | … | … |
| Telegram 429 / 5xx | … | … |
| Bot removed from chat | … | … |
| Process restarts mid-send | … | … |
| Invalid admin input | … | … |

## 10. Security and privacy

- Authorization:
- Input validation:
- Secrets handling:
- Data stored / not stored:
- Audit events:
- Threat-model changes:

## 11. Observability

- Log events and required fields:
- Metrics (name, type, labels):
- Health checks:
- Admin alerts:
- SLO impact:

## 12. Test plan

- Unit tests:
- Integration tests:
- End-to-end / manual checks:
- Coverage expectation (100% line and branch):
- Regression tests for fixed bugs:

## 13. Rollout and rollback

- Deployment steps:
- Migration/backup requirements:
- Rollback steps:
- Data-loss risk:

## 14. Clarifications (G1)

| # | Question | Answer | Answered by | Date |
|---|---|---|---|---|
| Q-1 | … | … | @… | … |

**G1 approval:** `GATE G1 APPROVED — @<maintainer> — <date>`

## 15. Change log

| Date | Change | Reason | Approved by |
|---|---|---|---|
| … | … | … | … |

## 16. Gate record

| Gate | Artifact | Approved by | Date | Comment link |
|---|---|---|---|---|
| G1 | Clarifications | | | |
| G2 | Spec + plan + tasks | | | |
| G3 | Code review | | | |
| G4 | Reconciliation | | | |
