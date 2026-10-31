# ADR-0002: Telegram-only administration, no web UI

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Decision owner | Project maintainers |
| Supersedes | None |
| Related | SPEC-001 (planned), CONSTITUTION.md §1.3, §6.1, §7 |

## Context

Administrators need to create and edit polls, change schedules, and register target chats. The initial
brief mentioned a UI for changing schedules and poll descriptions/options. Candidate interfaces were a
browser-based admin panel, a CLI, and Telegram chat commands.

The deployment target is a single VPS with no public web surface. The number of administrators is small
(one or two). The data is sensitive only at the level of chat IDs and poll content; there are no
end-user accounts.

## Decision

We will use **Telegram direct messages as the only administrative interface**.

- Access is controlled by a numeric user-ID allowlist in `ADMIN_USER_IDS`.
- Commands are accepted only in private chats; commands in groups/channels are ignored.
- Multi-field edits use guided inline-keyboard wizards; shortcuts exist for common actions.
- Destructive actions require an explicit inline confirmation.
- There is no web server, browser UI, REST API, or public HTTP endpoint.
- Health and metrics are served by a loopback-only sidecar for the operator, not for administration.

## Alternatives considered

| Alternative | Pros | Cons | Why not chosen |
|---|---|---|---|
| Web admin UI (FastAPI + HTMX/React) | Rich forms, bulk editing, dashboards | Auth, sessions, CSRF, TLS, frontend build, public surface, more tests | Disproportionate for one or two admins; conflicts with the no-public-surface goal. |
| CLI over SSH | Scriptable, precise | Poor mobile UX, no rich confirmation, operator must have shell access | Telegram is already the delivery channel and is available on mobile. |
| Telegram admin commands without wizards | Fast for experts | Error-prone for 12 options, time zones, and schedules | Wizards are safer; shortcuts remain available. |
| Hybrid web + Telegram | Best of both | Two interfaces to secure, test, and document | Scope creep; defer until a demonstrated need. |

## Consequences

### Positive

- No web attack surface, no session or CSRF handling, no TLS/reverse proxy requirement.
- Admins can operate from the same app they use for polls.
- Authorization is a simple allowlist; the audit trail maps directly to Telegram user IDs.
- Smaller dependency set and smaller container image.

### Negative

- Editing long option lists and schedules in chat is slower than a form.
- Telegram message and callback-data limits constrain the UI (4,096 chars, 64-byte callback data).
- Admin onboarding requires pressing Start in a DM; a bot cannot initiate a DM.
- Changing the allowlist requires an environment update and restart.
- No bulk import/export or dashboard in v1.

### Neutral / follow-up work

- Revisit a web UI only with a spec, ADR, and a security design for auth, CSRF, and TLS.
- If runtime admin management is added, it needs a secure bootstrap and audit design.
- Keep handlers thin so a future web adapter could reuse the same services.

## Compliance and verification

- `CONSTITUTION.md` §1.3, §6.1, §7, and §10.2 encode the policy.
- Tests cover allowlisted/non-allowlisted DM, group commands ignored, and callback authorization.
- CI has no web server to build or scan; the container exposes only loopback health/metrics.
- The runbook documents admin lockout recovery and onboarding.

## Links

- Issue: TBD
- Related: ADR-0001
