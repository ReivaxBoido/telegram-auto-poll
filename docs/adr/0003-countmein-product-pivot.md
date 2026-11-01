# ADR-0003: Product pivot to CountMeIn-style sign-up polls

| Field | Value |
|---|---|
| Status | Accepted — G2 approved 2026-11-02 |
| Date | 2026-11-02 |
| Decision owner | @ReivaxBoido |
| Supersedes | The native-poll product direction of SPEC-001 and SPEC-002 (not ADR-0001/0002) |
| Related | SPEC-003, issue #7, ADR-0001, ADR-0002, ADR-0004 |

## Context

The application was ratified as a private native-Telegram-poll publisher: `sendPoll` with a question,
2–12 options, anonymity flags, and a scheduler. The maintainer now wants the behaviour of
[@CountMeInBot](https://t.me/CountMeInBot): a bot-authored message with one button per option where
participants add or remove their own names, and the message is edited live to show the name list and
count. The same poll must also be publishable automatically at set intervals.

The pivot conflicts with the ratified constitution:

- §1.1 and §1.2 define the product as native polls.
- §1.3 excludes poll results ingestion and vote analytics.
- C-7 and §10.5 say the bot never reads or stores individual votes.
- §6.1 and §10.2 require every callback to come from an allowlisted admin.
- §5.2 stores options as a JSON array and has no respondent table.

G1 on 2026-11-02 confirmed: replace native polls with CountMeIn mode; keep the admin allowlist;
anyone in a target chat may vote; multi-select; first name only; 1–10 free options; registered chats
plus inline mode; responses global per occurrence; manual close/reopen; no production data; three
specs (core, scheduled auto-post, inline/lifecycle polish).

## Decision

We will **replace native poll delivery with CountMeIn-style sign-up polls** and store the minimum
respondent data needed to render them.

Key details:

1. A poll is a title plus 1–10 ordered options. An **instance** is one publication of the poll. A
   **message** is one bot message per instance/chat. A **response** is a user's membership in one
   option of one instance.
2. Any member of a target chat can toggle their own response through inline buttons. Responses are
   multi-select and global to the instance; every message for that instance shows the same list.
3. The bot stores only Telegram user ID, a first-name snapshot, and timestamps per response. It does
   not store usernames, last names, native poll results, or vote history.
4. Publishing uses `sendMessage`; updates use `editMessageText`. `sendPoll` and native poll result
   APIs are removed from the application.
5. SPEC-003 delivers manual publishing only. SPEC-004 adds recurrence on the same instance/message
   model. SPEC-005 adds inline mode and poll editing. v0.3.0-alpha is an integration milestone and is
   not a production replacement until SPEC-004 restores scheduling.
6. The schema reset is destructive. `0002_countmein_core` aborts if the old tables contain rows and
   `ALLOW_DESTRUCTIVE_MIGRATION` is not `true`; the pre-migration backup is mandatory.
7. The constitution is amended to v2.0.0 (MAJOR) and ADR-0004 defines the participant authorization
   model.

## Alternatives considered

| Alternative | Pros | Cons | Why not chosen |
|---|---|---|---|
| Keep native polls and add an RSVP message | No vote storage; small change | Not CountMeIn parity; names are not attached to options; two products | G1 Q-1 chose replacement. |
| Add CountMeIn as a second poll type | Backward compatible | Two schemas, two UX paths, double the test matrix, native path becomes dead weight | No production data; replacement is simpler. |
| Public multi-tenant CountMeIn | Full parity | Per-user ownership, abuse, quotas, privacy, scaling, billing surface | G1 Q-2 chose admin allowlist only. |
| Per-chat response lists | No cross-chat leakage | A poll posted to several chats shows different lists; not parity | G1 Q-8 chose global per occurrence. |
| Store names only in Telegram message text | No database | Cannot toggle, count, or redact reliably; message text is not a database | A response table is required. |
| Hard-delete polls and messages | Simple | Loses audit/history; Telegram copies remain; destructive | Soft-delete + response deletion + redaction. |
| Keep the native scheduler running | No regression window | It sends native polls that no longer exist | Heartbeat only; SPEC-004 restores scheduling on the new model. |
| Do nothing | No migration risk | Maintainer cannot run the desired product | G1 chose the pivot. |

## Consequences

### Positive

- The bot matches the requested CountMeIn interaction: one tap, live names, no chat noise.
- The instance/message/response model supports both manual and scheduled publishing without a second
  data model.
- Respondent data is deliberately minimal; deletion and retention are explicit.
- The admin allowlist and DM-only command surface remain, so the security model stays small.
- The existing infrastructure (single container, SQLite WAL, clock, logging, metrics, health, Docker,
  CI, backup, single-instance lock) is reused.

### Negative

- The constitution must change at MAJOR level; prior specs no longer describe the product.
- v0.3.0-alpha temporarily loses scheduling; operators must not deploy it as a production replacement.
- Responses are personal data. Cross-chat visibility means a name entered in one chat appears in every
  target chat for that instance; the publish confirmation and runbook must state this.
- The destructive migration requires a backup and an explicit flag; rollback requires a restore.
- A large amount of native-poll code and its tests are deleted, which is a one-time review cost.

### Neutral / follow-up work

- SPEC-004 adds recurrence and restores the scheduler heartbeat and schedule commands.
- SPEC-005 adds inline mode and poll editing.
- Per-chat response isolation, capacity/waitlists, and response export are future specs if needed.
- The metric catalog and glossary are updated in the same amendment.

## Compliance and verification

- `CONSTITUTION.md` v2.0.0 encodes the new mission, C-7, data model, auth, retention, and metrics.
- `specs/003-countmein-core/spec.md` maps every FR/NFR to tests; the traceability file is completed at
  G4.
- Tests assert that no `sendPoll` call exists, that votes do not create audit rows, that names never
  appear in logs/metrics, and that deletion removes responses and redacts messages.
- CI runs the destructive-migration guard test, the 100% coverage gate, gitleaks, bandit, pip-audit,
  CodeQL, and Trivy.
- The runbook documents the backup, the one-time flag, rollback, and the deletion procedure.

## Links

- Spec: [`specs/003-countmein-core/spec.md`](../../specs/003-countmein-core/spec.md)
- Issue: #7
- Related ADRs: ADR-0001 (stack), ADR-0002 (Telegram-only admin), ADR-0004 (participant callbacks)
