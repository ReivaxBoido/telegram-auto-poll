# SPEC-003: CountMeIn core — sign-up polls, live responses, and registered-chat publishing

| Field | Value |
|---|---|
| Spec ID | SPEC-003 |
| Status | Approved — G2 approved 2026-11-02 |
| Owner | @ReivaxBoido |
| Issue | #7 |
| Plan | [`plan.md`](plan.md) — awaiting G2 |
| Tasks | [`tasks.md`](tasks.md) — awaiting G2 |
| Target release | v0.3.0-alpha (core milestone; scheduling returns in SPEC-004 / v0.4.0) |
| Delivered in | TBD |
| Supersedes | The native-poll direction of SPEC-001 and SPEC-002 (see §17) |

> **Gate status:** G1 approved and G2 approved on 2026-11-02. Implementation may begin under the task
> list. This spec is the first of three; it deliberately does **not** implement scheduling or inline
> mode.

## 1. Problem

The application currently publishes native Telegram polls on a schedule. The maintainer wants it to
behave like [@CountMeInBot](https://t.me/CountMeInBot): a bot-authored sign-up message where each
participant taps an option and their name is added to (or removed from) that option, with the message
updated live for everyone. The same poll must also be publishable automatically at set intervals
(SPEC-004).

CountMeIn-style polls require the bot to store individual respondents, let non-admin chat members
mutate a message, and edit bot-authored messages instead of calling `sendPoll`. Those requirements
contradict the ratified constitution (§1.3, C-7, §10.5, §6.1). The maintainer approved the product
pivot at G1; this spec and the accompanying amendment/ADRs define the new contract.

Without this feature the bot cannot collect names, cannot show live counts, and has no foundation for
scheduled sign-up posts.

## 2. Goals

- **G-1:** An allowlisted admin can create a poll in a DM with a title and 1–10 options, preview it,
  and publish it to one or more registered chats.
- **G-2:** Any member of a target chat can tap an option to add or remove their first name; every
  message for that poll instance reflects the change within 2 seconds (p95).
- **G-3:** Responses are global to a poll instance and multi-select: a user may join several options,
  and all target chats for the instance see the same respondent list.
- **G-4:** The author can close, reopen, refresh, and delete a poll; deletion removes response data and
  redacts every posted message.
- **G-5:** Publishing is at-most-once per instance and chat, with bounded retries, crash recovery, and
  one admin alert per terminal failure.
- **G-6:** Privacy is minimised: only Telegram user ID, a first-name snapshot, and timestamps are
  stored; no usernames, last names, native poll results, or vote history.
- **G-7:** The delivered code passes the constitution's quality gates: `ruff`, `mypy --strict`,
  `pytest` at 100% line and branch coverage, plus security and container checks.

## 3. Non-goals

| ID | Non-goal | Why / when revisited |
|---|---|---|
| NG-1 | Recurrence, schedules, automatic interval posting | SPEC-004. The data model and posting pipeline are prepared, but no schedule table or commands ship here. |
| NG-2 | Inline mode (`@bot` in any chat) and inline-message editing | SPEC-005. The callback and message model reserve `inline_message_id`, but no inline handler ships here. |
| NG-3 | Editing a poll title or options after creation | SPEC-005. Create a new poll or delete and recreate in the interim. |
| NG-4 | Capacity limits, waitlists, payments, ticketing, quizzes, media, native polls | Not part of the CountMeIn core. Native `sendPoll` is removed. |
| NG-5 | Web UI, REST API, webhooks, multi-platform support | Constitution §1.3 and ADR-0002 remain in force. |
| NG-6 | Public multi-tenant use; any Telegram user creating polls | G1 Q-2: admin allowlist only. Revisit only with a new spec and abuse model. |
| NG-7 | Per-chat response isolation | G1 Q-8: responses are global per occurrence. Cross-chat visibility is documented in §10. |
| NG-8 | Internationalisation | Constitution §1.3. English only. |
| NG-9 | Reading native poll results or `getPollResults` | Still forbidden. This feature stores its own responses, not native poll votes. |

## 4. Users and stories

| Persona | Story |
|---|---|
| Admin | As an admin, I want to create a named sign-up list with several options and publish it to my groups, so that people can commit without typing in the chat. |
| Admin | As an admin, I want to see live counts and close the list when it is full or the deadline passes. |
| Admin | As an admin, I want to delete a poll and know its names are removed, so that I can honour a deletion request. |
| Participant | As a group member, I want to tap an option to add my name and tap again to remove it, without learning commands. |
| Participant | As a group member, I want to see who else is in, so that I can coordinate. |
| Operator | As an operator, I want the same single-container deployment, health checks, metrics, backups, and rollback discipline as before. |

## 5. Functional requirements

IDs are document-scoped and start at 301 to avoid colliding with SPEC-001/002.

### 5.1 Configuration, migration, and lifecycle

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-301 | Configuration MUST add `RESPONSE_NAMES_PER_OPTION` (default 50), `RESPONSE_UPDATE_DEBOUNCE_SECONDS` (default 1.0), `VOTES_PER_MINUTE` (default 30), `RESPONSE_RETENTION_DAYS` (default 90), `MESSAGE_EDIT_CONCURRENCY` (default 4), `MESSAGE_EDIT_RETRY_ATTEMPTS` (default 3), `POSTING_TICK_SECONDS` (default 5), and `ALLOW_DESTRUCTIVE_MIGRATION` (default false), validating every value at startup. | P0 | Given an out-of-range value, when the process starts, then it exits non-zero and names the variable without printing a secret. |
| FR-302 | Migration `0002` MUST take the existing pre-migration backup, preserve `chats` and `audit_events`, and replace the native-poll tables (`polls`, `poll_targets`, `schedules`, `deliveries`) with the new schema. If any old table contains rows and `ALLOW_DESTRUCTIVE_MIGRATION` is not `true`, the migration MUST abort without changing data. | P0 | Given a 0001 database with poll rows and the flag unset, when migration runs, then it aborts and the backup remains. Given the flag set, then the new schema exists and `chats`/`audit_events` rows survive. |
| FR-303 | Startup MUST recover interrupted posts: `sending` messages become `failed` with `ambiguous_outcome_after_restart`; `pending` messages remain claimable; `sent` messages are left untouched. | P0 | Given a `sending` row at startup, when recovery runs, then the row is `failed`, `completed_at` is set, and no send is attempted. |
| FR-304 | Shutdown MUST stop accepting updates and new posts, flush pending response broadcasts for at most `SHUTDOWN_GRACE_SECONDS`, checkpoint WAL, release the lock, and exit 0. | P0 | Given a pending broadcast, when `SIGTERM` arrives, then the edit completes or is abandoned within the grace period and the process exits 0. |
| FR-305 | The runtime MUST replace the native-poll scheduler and `sendPoll` delivery worker with a posting worker that sends and edits bot-authored text messages. The scheduler component MAY remain as a heartbeat only until SPEC-004. | P0 | Given a publish, when the worker runs, then `sendMessage` is called and no `sendPoll` call exists in the codebase. |

### 5.2 Poll creation and management

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-306 | `/newpoll` MUST run a guided DM wizard collecting a title (1–300 characters), 1–10 options (each 1–100 characters, unique after trimming and case folding), and a preview. | P0 | Given a valid run, when the admin confirms, then one poll and its options are committed in one transaction and `poll.create` is audited. |
| FR-307 | Invalid input MUST re-prompt with the exact constraint and current length, preserving earlier answers. | P1 | Given a 301-character title, then the reply states the 300 limit and the current length and stays on the title step. |
| FR-308 | `/polls [page]` MUST list polls with status, latest instance state, and total responses, 8 per page. `/poll <id>` MUST show the title, numbered options, latest instance, counts, and inline actions. | P1 | Given 9 polls, then page 1 shows 8 and page 2 shows 1. |
| FR-309 | `/publish <poll_id>` MUST show active registered chats as a multi-select (6 per page), require at least one, and require confirmation. On confirm it MUST create one open instance and one pending message row per selected chat in one transaction, then audit `poll.publish`. | P0 | Given 2 selected chats, when confirmed, then exactly one instance and two pending message rows exist. |
| FR-310 | Each `/publish` MUST create a new manual instance; publishing the same poll again is allowed after confirmation and never reuses an earlier instance. | P1 | Given a published poll, when `/publish` is confirmed again, then a second instance exists with its own responses. |
| FR-311 | `/close <poll_id>` MUST close the latest open instance after confirmation, mark its pending messages `skipped` with reason `instance_closed`, edit sent messages to the closed rendering, and remove their keyboards. | P0 | Given an open instance with one sent message, when closed, then the instance is `closed`, the message has no keyboard, and the text says voting is closed. |
| FR-312 | `/reopen <poll_id>` MUST reopen the latest closed instance after confirmation and restore the vote keyboard on sent messages. It MUST NOT resend messages that were skipped or missing. | P1 | Given a closed instance, when reopened, then votes are accepted again and the keyboard is restored. |
| FR-313 | `/deletepoll <poll_id>` MUST require confirmation, soft-delete the poll (`status='deleted'`), close all open instances, delete all response rows for those instances, edit every sent message to a redacted “This poll was deleted.” rendering with no keyboard, and audit `poll.delete`. | P0 | Given a poll with responses, when deleted, then no response rows remain, the messages show no names, and the poll row is retained with `status='deleted'`. |
| FR-314 | `/refresh <poll_id>` MUST re-render every sent message for the latest instance, retry pending/failed messages, and report missing or skipped messages. It MUST NOT create a new instance. | P1 | Given a stale message and a failed message, when refreshed, then the stale message is edited and the failed message is retried. |
| FR-315 | `/responses <poll_id>` MUST show the full name list per option for the latest instance, paginated (20 names per page), to admins only in DM. | P1 | Given 45 names in one option, then three pages are shown and no name is lost. |
| FR-316 | `/cancel` MUST cancel the active wizard or confirmation without committing. | P1 | Given an open wizard, when `/cancel` is sent, then no mutation occurs and the wizard state is discarded. |

### 5.3 Participant voting

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-317 | Every sent poll message MUST carry one inline button per option. Callback data MUST be versioned, ≤64 bytes, and contain only identifiers, never names or labels. | P0 | Given a poll with 10 options, then every generated callback is ≤64 bytes and round-trips through the codec. |
| FR-318 | Any user who can press a button in a target chat MUST be able to toggle their own response, without being in `ADMIN_USER_IDS`. | P0 | Given a non-admin callback for a valid open message, then the response is created and no admin check is applied. |
| FR-319 | The bot MUST validate the callback against the stored message: the instance exists, the instance is open, the poll is not deleted, the option belongs to the poll, and the callback's chat/message (or inline message) matches a `poll_messages` row. | P0 | Given a callback for another chat's message, then it is refused with no state change. |
| FR-320 | Toggling on MUST insert a response with the Telegram user ID and first-name snapshot; toggling off MUST delete it. A user MAY hold responses in several options of the same instance. | P0 | Given two options, when a user taps both, then two rows exist; tapping one again removes only that row. |
| FR-321 | The callback MUST be answered within 3 seconds with `Added to <option>`, `Removed from <option>`, `Voting is closed`, or a specific refusal. | P0 | Given a closed instance, then the answer is `Voting is closed` and no row changes. |
| FR-322 | After a successful toggle, every sent message for the instance MUST be edited to the new rendering within 2 seconds p95. Rapid toggles MUST be coalesced so that at most one edit per message is sent per debounce window. | P0 | Given five toggles within the debounce window, then one edit per message is observed. |
| FR-323 | Votes MUST be rate-limited per user to `VOTES_PER_MINUTE` (default 30). Excess votes MUST be answered with the retry-after seconds and MUST NOT mutate state. | P1 | Given 31 votes in a minute, then the 31st is refused and no row changes. |
| FR-324 | Duplicate first names MUST be allowed. Names are snapshots taken at response time; a later profile change MUST NOT rewrite existing rows. No username or last name is stored or displayed. | P1 | Given two users named “Alex”, then both names appear; changing a profile does not alter stored rows. |
| FR-325 | A vote on a deleted poll, closed instance, missing message, or unknown option MUST be refused without mutating state. | P0 | Given a deleted poll, then the answer is a refusal and no row is created. |

### 5.4 Rendering and message updates

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-326 | The renderer MUST show the title, each option as `label (N👥)` followed by up to `RESPONSE_NAMES_PER_OPTION` first names on separate lines, `... +N more` when truncated, and a final `👥 N people responded` line. It MUST use plain text, no parse mode. | P0 | Given 12 names and a limit of 10, then 10 names and `... +2 more` are shown. |
| FR-327 | The rendered text MUST never exceed 4,096 characters. If it would, the renderer MUST reduce the number of names per option (10 → 5 → 3 → 1 → 0) until it fits. It MUST NOT silently truncate the title or option labels. | P0 | Given 10 options with 10 long names each, then the output is ≤4,096 and the title/options are intact. |
| FR-328 | A closed instance MUST render `Voting closed.` and no keyboard. A deleted poll MUST render `This poll was deleted.` with no names and no keyboard. | P0 | Given each state, then the exact rendering is produced and the keyboard is absent. |
| FR-329 | Message edits MUST treat Telegram `message is not modified` as success, mark `message to edit not found` as `missing`, honour `429 retry_after` (clamped to 300 s), and retry 5xx/timeouts with bounded backoff. | P0 | Given each error, then the expected status/retry is recorded and no unhandled exception escapes. |
| FR-330 | The broadcaster MUST use bounded concurrency (`MESSAGE_EDIT_CONCURRENCY`) and MUST NOT block the event loop or hold a database transaction during a Telegram call. | P1 | Given 20 messages, then at most 4 edits are in flight and no transaction is open across an await. |

### 5.5 Posting worker and delivery guarantees

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-331 | The posting worker MUST claim pending messages whose `next_attempt_at_utc` is null or past, re-check that the poll, instance, and chat are active, and call `sendMessage` with the rendered text and vote keyboard. | P0 | Given a pending row, then the fake gateway records the exact text/keyboard and the row becomes `sent` with a message ID. |
| FR-332 | Sending MUST respect the existing per-chat (1/s), per-group (20/min), and global (25/s) limits with `SEND_CONCURRENCY`; a `429` MUST honour `retry_after` clamped to 300 s. | P1 | Given three sends to one chat, then they are spaced by at least 1 s of fake time. |
| FR-333 | Retryable failures MUST retry up to `SEND_RETRY_ATTEMPTS` with exponential backoff and ±20% jitter; non-retryable failures MUST fail immediately; terminal failures MUST alert all admins once per message row. | P0 | Given two timeouts then success, then one `sent` row with `attempt_count=3`. Given `chat not found`, then `failed` with one alert. |
| FR-334 | An inactive chat or a closed/deleted instance MUST mark a pending message `skipped` with a reason and MUST NOT send. | P0 | Given an inactive chat, then the row is `skipped` and no gateway call occurs. |
| FR-335 | At most one message row per `(instance_id, chat_id)` MUST exist; a duplicate claim MUST be a no-op. | P0 | Given a repeated worker tick, then the row count and send count are unchanged. |

### 5.6 Audit, retention, and observability

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-336 | Every admin mutation (create, publish, close, reopen, delete, refresh, chat register/deactivate) MUST write an immutable `audit_events` row in the same transaction. Votes MUST NOT be audit events. | P1 | Given a publish, then one `poll.publish` row exists; given a vote, then no audit row exists. |
| FR-337 | A daily job at 03:15 UTC MUST prune responses for closed/deleted instances older than `RESPONSE_RETENTION_DAYS`, redact their sent messages first, prune old message/audit rows per `RETENTION_DAYS`, and checkpoint WAL. It MUST be idempotent and batched. | P1 | Given an expired closed instance, then its responses are gone, its message is redacted, and a second run deletes 0. |
| FR-338 | `/status` MUST report version, environment, uptime, lock state, DB check, posting-worker heartbeat, counts (chats, polls, open instances, pending messages, active responses), and the last five failed messages, without names. | P1 | Given a healthy instance, then every field is present and no respondent name appears. |
| FR-339 | `/livez`, `/healthz`, and `/metrics` MUST remain available. `/healthz` MUST require a DB `SELECT 1` within 1 s and a posting-worker heartbeat younger than three `POSTING_TICK_SECONDS`; the scheduler heartbeat is not required until SPEC-004. | P1 | Given a stalled posting worker, then `/healthz` returns 503 and names the worker reason. |
| FR-340 | Logs MUST be one JSON object per line with the existing required fields. They MUST NOT contain respondent names, participant user IDs, raw updates, or tokens; vote logs carry counts and option IDs only. | P0 | Given a vote and an edit, then every line parses as JSON and no name or participant ID appears. |
| FR-341 | The Docker image, Compose contract, non-root user, read-only root filesystem, loopback health port, and single-instance lock MUST remain unchanged except for the new environment variables. | P0 | Given `docker compose up -d`, then the container is healthy and `/version` matches the build args. |

## 6. Non-functional requirements

| ID | Requirement | Target | Verification |
|---|---|---|---|
| NFR-301 | Coverage | 100% line and branch of `src/telegram_auto_poll`, excluding only constitution §11.3 cases | `pytest --cov --cov-branch --cov-fail-under=100` |
| NFR-302 | Vote-to-visible-edit latency | p95 ≤ 2 s, p99 ≤ 5 s, excluding Telegram outages | fake-clock broadcaster tests + `tap_vote_to_edit_seconds` histogram |
| NFR-303 | Duplicate posts | 0 per instance/chat per release | unique-index integration test + reconciliation |
| NFR-304 | Test isolation | No network, no real clock, no sleeps | socket-blocking fixture + fake clock/gateway |
| NFR-305 | Message-edit throughput | Coalesce bursts; bounded concurrency 4; no event-loop blocking | load-style broadcaster test with 100 toggles |
| NFR-306 | Privacy | Only user ID, first name, timestamps; no names in logs/metrics; deletion removes responses | repository inspection + log-capture test + deletion test |
| NFR-307 | Admin responsiveness | p95 ≤ 2 s for read-only commands, excluding Telegram network | integration timing test |
| NFR-308 | Capacity | 50 chats, 200 polls, 1,000 instances, 500 posts/day, 10,000 responses, DB < 100 MB | synthetic seed + query-plan review |
| NFR-309 | Resource envelope | ≤ 256 MB RSS steady state; ≤ 0.25 vCPU average | manual measurement recorded in the PR |
| NFR-310 | Startup | ≤ 15 s including migrations on the reference dataset | integration test + manual check |
| NFR-311 | Dependencies | No new runtime dependency; standard library and existing lockfile only | `uv.lock` unchanged unless approved |
| NFR-312 | Rollback | Previous image plus pre-migration backup; new schema is not backward-compatible with v0.1.0 | rehearsal documented in the runbook |

## 7. Data model changes

### 7.1 New tables

| Table | Purpose | Key constraints |
|---|---|---|
| `polls` | Poll definition and lifecycle | `status IN ('draft','active','deleted')`; `length(title) BETWEEN 1 AND 300`; index `(status, created_at)` |
| `poll_options` | Ordered options | `UNIQUE(poll_id, position)`; `UNIQUE(poll_id, label_folded)`; `position BETWEEN 0 AND 9`; `length(label) BETWEEN 1 AND 100` |
| `poll_instances` | One publication/occurrence of a poll | `status IN ('open','closed','deleted')`; `trigger IN ('manual','scheduled')`; index `(poll_id, created_at)`; `(status)` |
| `poll_messages` | One bot message per instance/chat (or inline message) | partial `UNIQUE(instance_id, chat_id)`; partial `UNIQUE(instance_id, inline_message_id)`; `status IN ('pending','sending','sent','failed','missing','skipped')`; index `(status, next_attempt_at_utc)` |
| `poll_responses` | A user's membership in an option | `UNIQUE(instance_id, option_id, telegram_user_id)`; index `(instance_id, option_id)`; `length(first_name) BETWEEN 1 AND 64` |

`chats` and `audit_events` are preserved. `audit_events.entity_type` is widened to include
`poll_instance`. The old `polls`, `poll_targets`, `schedules`, and `deliveries` tables are dropped
after the destructive-reset guard in FR-302.

### 7.2 Invariants

| ID | Invariant |
|---|---|
| I-16 | A poll title is 1–300 characters; every option label is 1–100; a poll has 1–10 options. |
| I-17 | Option labels are unique within a poll after trimming and case folding (`label_folded`). |
| I-18 | A poll in `deleted` status has no open instance and no response rows. |
| I-19 | An instance belongs to exactly one poll; a `manual` instance has `scheduled_for_utc` null. |
| I-20 | An open instance accepts responses; a closed or deleted instance does not. |
| I-21 | A response references an option of the same poll as its instance. |
| I-22 | A user has at most one response per option per instance. |
| I-23 | A message row belongs to exactly one instance and has either a `chat_id` or an `inline_message_id` (not both). |
| I-24 | A `sent` message has `telegram_message_id` and `sent_at`; a `failed`/`skipped` message has `last_error` or a skip reason. |
| I-25 | A `missing` message has no vote keyboard and is not retried automatically. |
| I-26 | A closed instance has `closed_at`; an open instance has it null. |
| I-27 | Response `first_name` is a 1–64 character snapshot; no username or last name is stored. |
| I-28 | All epoch-second columns are UTC; the injected `Clock` supplies every now-read. |
| I-29 | No table stores tokens, raw updates, native poll results, or voter history beyond the current response set. |
| I-30 | A deleted poll's messages render no names and carry no keyboard. |

### 7.3 Migration strategy

- One forward-only migration `0002_countmein_core`, applied after the existing pre-migration backup.
- Destructive reset guarded by `ALLOW_DESTRUCTIVE_MIGRATION`; if old tables have rows and the flag is
  false, abort with an actionable message. No backfill: G1 Q-10 confirmed there is no production data.
- Tested on an empty database and on a 0001 database seeded with chats, polls, schedules, deliveries,
  and audit rows. `chats` and `audit_events` must survive.
- Rollback is restore-from-backup plus the previous image; the new schema is not readable by v0.1.0.
- Retention: responses for closed/deleted instances after `RESPONSE_RETENTION_DAYS`; messages/audit
  after `RETENTION_DAYS`; poll definitions retained until deleted.

## 8. Telegram UX

### 8.1 Commands

| Command | Purpose |
|---|---|
| `/start` | Identify the admin and show the menu. |
| `/help` | List commands and explain the sign-up model. |
| `/version` | Show version and build. |
| `/status` | Show health, counts, and recent failures. |
| `/chats [page]` | List registered chats. |
| `/addchat` | Register a chat by forward or numeric ID. |
| `/removechat <id>` | Deactivate a chat after confirmation. |
| `/polls [page]` | List polls. |
| `/poll <id>` | Poll detail with inline actions. |
| `/newpoll` | Create a poll wizard. |
| `/publish <id>` | Select target chats and publish a new instance. |
| `/close <id>` | Close the latest open instance. |
| `/reopen <id>` | Reopen the latest closed instance. |
| `/refresh <id>` | Re-render and retry messages for the latest instance. |
| `/responses <id>` | Full name list per option, paginated. |
| `/deletepoll <id>` | Soft-delete, remove responses, redact messages. |
| `/cancel` | Cancel the active wizard or confirmation. |

Removed in this spec: `/schedules`, `/schedule`, `/pause`, `/resume`, `/delete_schedule`, `/sendnow`,
`/resend`, `/archive`, `/unarchive`. SPEC-004 restores the schedule commands against the new model.

### 8.2 New poll wizard

1. Title (typed, 1–300; shows remaining characters).
2. Options (typed, 1–100 each; after each, show the numbered list with **Done**; enforce 1–10 and
   case-insensitive uniqueness). **Remove** is available from the list.
3. Preview: title, numbered options, and a note that publishing is a separate step.
4. Confirm: create the poll as `draft`; audit; show `/poll <id>` with a **Publish** button.

Every step has **Back** and **Cancel**. Invalid input re-prompts without losing earlier answers.

### 8.3 Publish flow

1. `/publish <id>` or the **Publish** button shows active registered chats, 6 per page, each button
   labelled with the chat title and type; selected state is visible.
2. **Done** requires at least one chat.
3. Confirmation shows the poll title, option count, target names, and a warning that responses are
   shared across all selected chats.
4. Confirm creates the instance and pending messages; the worker sends them.

### 8.4 Poll message and vote keyboard

```
Wed training @ MPSH

0700-1030 (2👥)
Alice
Bob

1045-1330 (1👥)
Carol

👥 3 people responded
```

- The title is the first line, followed by a blank line.
- Each option is rendered as `label (N👥)`, then one first name per line, then a blank line.
- When an option has more names than the display limit, the last line is `... +N more`.
- The footer is `👥 N people responded` (or `👥 1 person responded`, `👥 Nobody responded`).
- One button per option, two per row (maximum 5 rows for 10 options).
- Button text is the option label truncated to 60 characters with `…`; the message shows the full label.
- Closed: `Voting closed.` and no keyboard.
- Deleted: `This poll was deleted.` and no keyboard.
- Plain text only; no parse mode.

### 8.5 Callback data

| Namespace | Format | Example | Actor |
|---|---|---|---|
| Vote | `v1:v:<instance_id>:<option_id>` | `v1:v:12:3` | Any chat member |
| Poll action | `v1:p:<action>:<poll_id>` | `v1:p:close:12` | Admin in DM |
| Target toggle | `v1:t:<poll_id>:<chat_id>` | `v1:t:12:-1001` | Admin in DM |
| Wizard | `v1:w:<step>:<value>` | `v1:w:opt:done` | Admin in DM |
| Confirmation | `v1:c:<action>:<entity_id>` | `v1:c:del:12` | Admin in DM |

All values are integers or short enum tokens; every generated callback is ≤64 bytes.

### 8.6 Limits and empty/error states

- Messages ≤4,096 characters; lists paginate at 8 items (6 for chat selection); keyboards ≤8 rows and
  ≤4 buttons per row.
- Empty poll list explains `/newpoll`; empty chat list explains `/addchat`.
- A vote on a closed/deleted poll explains the state and offers no retry.
- Destructive actions (`/close`, `/deletepoll`) show a summary and a two-button confirmation.
- Unexpected errors show a correlation ID matching the structured log; never a stack trace.

## 9. Edge cases and failure modes

| Case | Expected behavior | Test |
|---|---|---|
| Title 0/1/300/301 characters | Reject at 0/301 with the exact limit; accept 1/300 | `test_poll_validation.py` |
| Options 0/1/10/11 | Reject 0/11; accept 1/10 | `test_poll_validation.py` |
| Duplicate/blank option | Reject with the duplicate/blank reason | `test_poll_validation.py` |
| Option label 100/101 | Accept 100; reject 101 | `test_poll_validation.py` |
| Non-admin vote | Accepted if the message/instance is valid | `test_vote_auth.py` |
| Forged callback for another chat | Refused; no state change | `test_vote_auth.py` |
| Vote on closed instance | Refused with `Voting is closed` | `test_response_service.py` |
| Multi-select then toggle off | Two rows, then one row | `test_response_service.py` |
| Duplicate first names | Both names rendered | `test_renderer.py` |
| 12 names, limit 10 | 10 names + `+2 more` | `test_renderer.py` |
| 10 options with long names | Output ≤4,096; names reduced, labels intact | `test_renderer.py` |
| Five rapid toggles | One edit per message per debounce window | `test_broadcaster.py` |
| `message is not modified` | Treated as success | `test_broadcaster.py` |
| `message to edit not found` | Marked `missing`, no retry | `test_broadcaster.py` |
| 429 `retry_after` | Retry at max(backoff, retry_after), capped 300 s | `test_broadcaster.py` |
| Chat inactive at post time | `skipped`, no send | `test_posting_worker.py` |
| Instance closed with pending messages | `skipped`, reason `instance_closed` | `test_posting_worker.py` |
| Crash after send, before commit | `sending` → `failed` on startup; no auto-retry | `test_recovery.py` |
| Duplicate worker tick | One row, one send | `test_posting_worker.py` |
| Poll deleted with responses | Responses deleted; messages redacted | `test_poll_service.py` |
| Retention on open instance | Responses retained | `test_retention.py` |
| Old tables have rows, flag false | Migration aborts, backup remains | `test_migration.py` |
| Second instance on same data dir | Exits non-zero, does not poll | `test_lock.py` |
| Invalid new config value | Startup aborts naming the variable | `test_config.py` |

## 10. Security and privacy

- **Admin authorization:** every command handler and admin callback checks private chat + allowlist
  (`ADMIN_USER_IDS`). Admin callbacks in groups/channels are rejected. The allowlist remains
  environment-only.
- **Participant authorization:** the vote namespace is intentionally not allowlisted. A vote is
  validated against the stored `poll_messages` row (chat/message or inline message), the open
  instance, and the option's poll. The actor is always `callback.from_user.id`; callback data is never
  trusted as identity. A forged callback can only change the sender's own response.
- **No membership lookup per vote:** Telegram only delivers callbacks to users who can see the
  message. The bot does not call `getChatMember` on every vote (latency and channel limitations);
  this is recorded in ADR-0004.
- **Input validation:** title, option labels, first names, IDs, and callback data are validated at the
  domain boundary. No user text is parsed as HTML.
- **Secrets:** the bot token is a `SecretStr`, redacted centrally, and never logged or stored. No new
  secret is introduced.
- **Data stored:** poll title/options, chat metadata, instance/message state, Telegram message IDs,
  and for each response: Telegram user ID, first-name snapshot, timestamps. No usernames, last names,
  raw updates, native poll results, or vote history.
- **Cross-chat visibility:** responses are global to an instance. A name entered in one target chat is
  visible in every target chat for that instance. The publish confirmation states this; the operator
  must only publish to chats in the same trust boundary. This is a deliberate G1 Q-8 decision.
- **Deletion:** `/deletepoll` deletes response rows and redacts messages. Retention does the same for
  closed instances after `RESPONSE_RETENTION_DAYS`. Telegram message copies already seen by users
  cannot be recalled; the spec states this limitation.
- **Audit:** admin mutations are audited; votes are not, to avoid a durable vote history. Failed
  authorization and rate-limit rejections are logged without names.
- **Threat model changes:** respondent data is personal data; forged callback, vote spam, message-edit
  abuse, and cross-chat leakage are added to the constitution §10.1 threat model.
- **Abuse controls:** per-user vote bucket, bounded edit concurrency, per-chat/global send limits, and
  admin confirmations for destructive actions.

## 11. Observability

- **Log events:** `poll_created`, `poll_published`, `poll_instance_created`, `poll_closed`,
  `poll_reopened`, `poll_deleted`, `poll_refreshed`, `poll_message_sent`, `poll_message_failed`,
  `poll_message_skipped`, `poll_message_missing`, `vote_added`, `vote_removed`, `vote_rejected`,
  `broadcast_coalesced`, `broadcast_flushed`, `message_edit_failed`, `responses_pruned`,
  `startup_recovery`, `shutdown_complete`.
- **Metrics:** `tap_polls_total{status}`, `tap_poll_instances_open`, `tap_poll_messages_total{status}`,
  `tap_votes_total{action}`, `tap_vote_rejections_total{reason}`,
  `tap_message_edits_total{status}`, `tap_broadcast_coalesced_total`, `tap_responses_active`,
  `tap_vote_to_edit_seconds`, `tap_response_retention_deleted_total{kind}`. Existing build, DB, bot
  polling, send duration, and admin command metrics remain. No chat/user IDs in labels.
- **Health:** `/livez` process-only; `/healthz` DB `SELECT 1` ≤1 s and posting-worker heartbeat
  <3 ticks; `/metrics` as before.
- **Admin alerts:** one alert per terminal post failure; one alert when `/refresh` finds missing
  messages; startup summary of ambiguous `sending` rows. No names in alerts.
- **SLO impact:** establishes the vote-to-edit p95 ≤2 s and zero-duplicate-post SLOs.

## 12. Test plan

### 12.1 Unit

- Validation boundaries for title, options, labels, first names, and callback data.
- Renderer: 0/1/N/>N names, duplicate names, long labels, 4,096 boundary, closed/deleted states.
- Poll/instance/message state machines and every transition.
- Vote toggling, multi-select, closed/deleted refusals, rate limiting.
- Callback codec: round-trip, version rejection, namespace routing, ≤64 bytes.
- Broadcaster: debounce, coalescing, dirty re-run, retry classification, `not modified`, `not found`,
  429, 5xx, shutdown flush.
- Posting worker: success, retry, exhaustion, non-retryable, inactive chat, closed instance, duplicate
  claim.
- Config parsing for every new variable and the destructive-migration guard.
- Audit event writing and absence for votes.
- Retention: open vs closed, redaction before deletion, idempotency, batching.

### 12.2 Integration

- Repositories against a temporary SQLite file with migrations; unique/FK enforcement.
- Migration 0002 on empty and 0001-seeded databases; `chats`/`audit_events` survive; guard aborts.
- Publish creates one instance and N pending messages; worker sends with a fake gateway.
- Vote in chat A edits messages in chats A and B (global instance).
- Close/reopen/delete edit all messages and remove/restore keyboards.
- `/livez`, `/healthz` 200/503, `/metrics` exposition via an aiohttp test client.
- Single-instance lock and graceful shutdown.

### 12.3 End-to-end / manual

- `docker compose up -d` against a dedicated test bot token and test group.
- Create a poll, publish it, vote from two accounts, confirm the live list and counts.
- Close, reopen, refresh, and delete; confirm the message state and data removal.
- Restart during a post and confirm no duplicate and no late send beyond the grace policy.

### 12.4 Coverage and quality

- 100% line and branch on `src/telegram_auto_poll`, exclusions limited to constitution §11.3.
- No network in unit/integration tests, enforced by a socket-blocking fixture.
- `ruff format --check`, `ruff check`, `mypy --strict` clean.
- No new dependency; `uv.lock` unchanged.

## 13. Rollout and rollback

- **Release:** v0.3.0-alpha. This is an integration milestone, not a production replacement: scheduling
  returns in v0.4.0 (SPEC-004). Do not deploy to production unless the operator accepts manual-only
  publishing for the interim.
- **Deployment:** merge, tag, CI publishes the GHCR image, operator runs `docker compose pull` and
  `docker compose up -d` after taking a backup.
- **Migration:** `0002` is destructive; the pre-migration backup is mandatory. Set
  `ALLOW_DESTRUCTIVE_MIGRATION=true` only for the one migration run if old rows exist; remove it
  afterwards.
- **Rollback:** redeploy v0.1.0 and restore the pre-migration backup. The new schema is not readable by
  the old image. Record the data-loss window (anything written after the backup).
- **First-run checklist:** `.env` with token/admin IDs, admin presses Start, `/addchat`, `/newpoll`,
  `/publish`, vote from a test account, `/close`, `/deletepoll`, verify `/status`.

## 14. Clarifications (G1)

| # | Question | Answer | Answered by | Date |
|---|---|---|---|---|
| Q-1 | The request reverses the constitution's mission and hard rules (C-7/§10.5 forbid storing votes; §1.3 excludes vote data). How should we proceed? | Replace with CountMeIn mode. Amend the constitution to v2.0.0 and add ADRs. | @ReivaxBoido | 2026-11-02 |
| Q-2 | Who may create and manage polls? | Admin allowlist only; ordinary chat members vote. | @ReivaxBoido | 2026-11-02 |
| Q-3 | What does “automatically send at set intervals” mean? | Recurring fresh sign-up post: each occurrence is a new instance with a fresh response list. | @ReivaxBoido | 2026-11-02 |
| Q-4 | Who can vote, and how? | Anyone in the chat; multi-select. | @ReivaxBoido | 2026-11-02 |
| Q-5 | What identity is shown next to a response? | First name only. | @ReivaxBoido | 2026-11-02 |
| Q-6 | What structure should a sign-up poll have? | Title + 1–10 free options. | @ReivaxBoido | 2026-11-02 |
| Q-7 | How are polls published? | Registered chats plus inline mode. Inline mode is deferred to SPEC-005; registered-chat publishing ships here. | @ReivaxBoido | 2026-11-02 |
| Q-8 | Should responses be shared across chats and occurrences? | Global per occurrence. All target chats for one instance share the response list; each new occurrence starts fresh. | @ReivaxBoido | 2026-11-02 |
| Q-9 | When does voting close? | Manual close by the author; reopenable. | @ReivaxBoido | 2026-11-02 |
| Q-10 | What should happen to existing native poll data? | No production data; rebuild the schema. | @ReivaxBoido | 2026-11-02 |
| Q-11 | How should the overhaul be split, and what about SPEC-002? | Three specs: core sign-up polls (this one), scheduled auto-post (SPEC-004), inline/lifecycle polish (SPEC-005). Supersede SPEC-002 and reuse its button components. | @ReivaxBoido | 2026-11-02 |

**G1 approval:** `GATE G1 APPROVED — @ReivaxBoido — 2026-11-02` (recorded in issue #7).

## 15. Change log

| Date | Change | Reason | Approved by |
|---|---|---|---|
| 2026-11-02 | Initial draft created from issue #7 and G1 answers Q-1–Q-11. | Start of SPEC-003. | — |
| 2026-11-02 | Split the overhaul into SPEC-003/004/005; deferred scheduling and inline mode. | G1 Q-3, Q-7, Q-11; keep the first review bounded. | @ReivaxBoido |

## 16. Gate record

| Gate | Artifact | Approved by | Date | Comment link |
|---|---|---|---|---|
| G1 | Clarifications (§14) | @ReivaxBoido | 2026-11-02 | issue #7, comment 6561766212 |
| G2 | `spec.md`, `plan.md`, `tasks.md`, constitution amendment, ADR-0003/0004 | @ReivaxBoido | 2026-11-02 | "gate 1 approved. gate 2 approved" |
| G3 | Code review | | | |
| G4 | Reconciliation | | | |

## 17. Roadmap and supersession

This spec is the first of three. The program replaces the native-poll direction of SPEC-001 and the
buttons-first native-poll wizard of SPEC-002.

| Spec | Scope | Depends on | Target |
|---|---|---|---|
| SPEC-003 (this) | Core sign-up polls: create, publish to registered chats, vote, live edit, close/reopen/delete, retention, observability. | Constitution amendment + ADR-0003/0004 | v0.3.0-alpha |
| SPEC-004 | Scheduled auto-post: recurrence (daily/weekly/monthly/once), per-occurrence instance, target chats, grace window, idempotency, schedule UX, restore the scheduler heartbeat. | SPEC-003 | v0.4.0 |
| SPEC-005 | Inline mode (`@bot` publishing and inline-message editing), poll editing, and lifecycle/admin polish. | SPEC-003, SPEC-004 | v0.5.0 |

**SPEC-002 handling:** issue #4 is superseded by issue #7. Its button components (target selection,
weekday selection, callback codec patterns) are reused where they fit; its native-poll wizard and
`sendPoll` assumptions are not. The branch is not merged; delete it only after the maintainer confirms.
