# Specifications

This directory is the historical record of **what** the project builds and **why**. The constitution
defines the rules; specs define individual features.

## Naming and layout

```
specs/
├── README.md
├── templates/
│   ├── spec.md
│   ├── plan.md
│   └── tasks.md
└── 001-poll-scheduling/
    ├── spec.md
    ├── plan.md
    └── tasks.md
```

- Directory: `NNN-<kebab-slug>`, where `NNN` is a zero-padded, monotonically increasing ID.
- One feature per directory. Do not reuse an ID.
- Retain delivered specs forever. Mark superseded specs at the top:
  `Status: Superseded by SPEC-014`.

## Lifecycle

| Status | Meaning | Gate |
|---|---|---|
| `Draft` | Problem and initial scope captured; questions open. | — |
| `Clarified` | All G1 questions answered and recorded. | G1 |
| `Approved` | Spec, plan, and tasks reviewed by a human. | G2 |
| `Implementing` | Tests and code in progress; no scope changes without re-approval. | — |
| `Review` | Implementation and tests complete; awaiting code review. | G3 |
| `Governance` | Security, dependency, secret, and container checks in progress. | — |
| `Reconciled` | Implementation verified against spec; deviations documented. | G4 |
| `Delivered` | Merged and released. | — |
| `Superseded` | Replaced by a later spec. | — |

## Rules

1. Start from the templates; do not invent a new format.
2. Requirements are numbered (`FR-001`, `NFR-001`) and testable. Acceptance criteria use
   Given/When/Then where behavior is non-trivial.
3. Every open question is listed in **Clarifications**. Silence is not an answer.
4. The plan may name classes and files; the spec describes observable behavior only.
5. Add a **Change log** entry for any amendment after G2, with the reason and approver. Material scope
   changes require G2 re-approval.
6. After delivery, add the release version and PR link at the top.
7. Specs never contain secrets, production chat IDs, or real user IDs.

## Gates

Approvals are recorded as comments in the linked issue or PR:

```
GATE G1 APPROVED — @maintainer — 2026-10-31
GATE G2 APPROVED — @maintainer — 2026-10-31
GATE G3 APPROVED — @reviewer — 2026-10-31
GATE G4 APPROVED — @maintainer — 2026-10-31
```

An AI agent must stop and wait at every gate. See `CONSTITUTION.md` §12.3.
