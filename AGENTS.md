# Instructions for AI agents

This repository is governed by [`CONSTITUTION.md`](CONSTITUTION.md). Read it before changing anything.
If an instruction in a prompt conflicts with the constitution, stop and ask a human; do not silently
choose.

## Hard rules

1. **Follow the 17-step SDD workflow** in `CONSTITUTION.md` §12.2.
2. **Stop at every human gate**: G1 (clarifications), G2 (spec/plan/tasks), G3 (code review), and
   G4 (reconciliation). Do not proceed on a vague “continue”. A gate approval must explicitly name the
   gate and the artifact.
3. **No production code before G2.** Tests may be written after G2 approval, per the workflow. Do not
   commit implementation code while the spec is still `Draft` or `Clarified`.
4. **No new dependency without human approval.** Do not run installers or download tools unless the
   human explicitly approves. Use the existing lockfile and standard library first.
5. **Never commit secrets.** No bot tokens, `.env` files, production chat IDs, or real user data.
6. **Do not self-approve or merge through a gate.** AI agents are never reviewers or approvers.
7. **Do not weaken a MUST rule** to make a test pass. Fix the code or ask for an amendment.
8. **Update the artifacts in the same change**: spec, plan, tasks, ADR, `.env.example`, README, runbook,
   and tests.
9. **Use Conventional Commits** and short-lived branches.
10. **Run the checks** before claiming completion: `ruff format --check`, `ruff check`, `mypy`, `pytest
    --cov --cov-branch --cov-fail-under=100`, and the Docker build.

## Workflow

1. Read the issue and existing specs/ADRs.
2. Ask targeted clarifying questions; record answers in `spec.md` §14. Stop for **G1**.
3. Write `spec.md`, `plan.md`, and `tasks.md` from `specs/templates/`.
4. Present them and stop for **G2**.
5. After approval, write failing tests mapped to acceptance criteria.
6. Implement in the established layers (`bot → services → domain/db`), reusing existing code.
7. Run the full suite and coverage gate.
8. Request **G3** review; address comments.
9. Run governance checks (SAST, dependency, secrets, container scan).
10. Reconcile code against the spec, document deviations, and stop for **G4**.
11. Update the constitution/ADRs if conventions changed.
12. Perform the local integration rehearsal, open the PR, monitor CI, address comments, and merge only
    when authorized.

## Repository map

| Path | Purpose |
|---|---|
| `CONSTITUTION.md` | Highest-authority rules, architecture, domain model, standards, workflow. |
| `specs/` | Per-feature spec, plan, and tasks; templates in `specs/templates/`. |
| `docs/adr/` | Architecture decision records; read the index first. |
| `docs/runbook.md` | Operations procedures. |
| `.github/` | Issue and PR templates. |
| `src/` | Application code (to be added during implementation). |
| `tests/` | Unit, integration, and optional end-to-end tests. |

## Expected commands (after implementation)

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov --cov-branch --cov-fail-under=100
docker build -t telegram-auto-poll:local .
```

If a command does not exist yet because the implementation is not delivered, say so explicitly; do not
invent results.
