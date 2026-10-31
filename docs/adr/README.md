# Architecture Decision Records

An ADR captures a significant decision: the context, the choice, the alternatives, and the
consequences. ADRs are immutable. To change a decision, add a new ADR that supersedes the old one.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-technology-stack.md) | Technology stack and runtime architecture | Accepted |
| [0002](0002-telegram-only-admin-interface.md) | Telegram-only administration, no web UI | Accepted |

## Process

1. Copy [`template.md`](template.md) to `NNNN-<kebab-slug>.md` using the next number.
2. Set status to `Proposed` and open a PR.
3. Discuss in the PR. A maintainer accepts or rejects it.
4. On acceptance, update the status and this index in the same PR.
5. If it supersedes an ADR, update the old ADR’s status to `Superseded by ADR-NNNN`.
6. If the decision changes a hard rule in the constitution, also submit a constitution amendment.

- ADRs are never deleted.
- Numbering is sequential and never reused.
- An ADR may be narrow; not every code change needs one. Use one when the decision is expensive to
  reverse, affects multiple modules, changes an external contract, or introduces a dependency.
- Statuses: `Proposed`, `Accepted`, `Rejected`, `Deprecated`, `Superseded by ADR-NNNN`.

## When an ADR is required

- A new runtime dependency or a change to a mandated technology.
- A change to the process model, deployment topology, or data store.
- A change to authorization, secret handling, or the public/private network surface.
- A schema change that is destructive or requires a multi-release migration.
- A deviation from a **MUST** rule in `CONSTITUTION.md`.
