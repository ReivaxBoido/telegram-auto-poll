## Summary

<!-- What does this PR change, and why? Link the approved artifacts. -->

- Issue: #
- Spec: `specs/NNN-<slug>/spec.md`
- Plan: `specs/NNN-<slug>/plan.md`
- ADR(s): `docs/adr/NNNN-...` (or "none")

## Type of change

- [ ] Feature (`feat`)
- [ ] Bug fix (`fix`)
- [ ] Refactor (`refactor`)
- [ ] Documentation (`docs`)
- [ ] Tests (`test`)
- [ ] Build/CI (`build`/`ci`)
- [ ] Chore (`chore`)
- [ ] Breaking change

## What changed

<!-- Bullet the observable changes. Call out anything a reviewer should scrutinize. -->

-
-

## Test evidence

```text
# Commands run and results
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov --cov-branch --cov-fail-under=100
```

- Coverage: <XX.X>% line / <XX.X>% branch
- New/updated tests:
- Manual Telegram checks (chat type, timezone, DST case):

## Telegram UX

<!-- For any command, wizard, message, or keyboard change, include a short transcript. -->

```text
Admin: /newpoll
Bot: ...
```

## Data, migration, and rollback

- Schema change: yes/no
- Migration tested on empty DB: yes/no
- Migration tested on previous schema: yes/no
- Pre-migration backup path:
- Rollback steps:
- Data-loss risk:

## Security and privacy

- Authorization checked on every new handler/callback: yes/no
- Input validation and length limits: yes/no
- Secrets or production data in diff/logs: no
- New dependency justified: yes/no/none
- Threat-model impact:

## Observability

- Logs added/updated:
- Metrics added/updated:
- Alerts added/updated:
- Runbook updated: yes/no/none

## Human gates

- [ ] G1 — Clarifications approved (link/comment)
- [ ] G2 — Spec, plan, and tasks approved before implementation (link/comment)
- [ ] G3 — Code review approved by a non-author (link/comment)
- [ ] G4 — Reconciliation approved (link/comment)

## Definition of Done

- [ ] Acceptance criteria covered by automated tests.
- [ ] Format, lint, types, and tests pass locally and in CI.
- [ ] Coverage is 100% line and branch for application code.
- [ ] No secrets, tokens, or production data.
- [ ] Migration included and tested, or not applicable.
- [ ] `.env.example`, README, runbook, and specs updated.
- [ ] Audit events added for new mutations.
- [ ] No `TODO`, `FIXME`, debug output, or commented-out code.
- [ ] Diff is within the 800-line review limit, or split is justified.
- [ ] Works in the Docker image, not only in the dev environment.

## Reviewer notes

<!-- Anything the reviewer should focus on, known limitations, or follow-up issues. -->
