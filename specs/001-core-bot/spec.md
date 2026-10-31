# SPEC-001: Core bot — scheduled poll delivery

| Field | Value |
|---|---|
| Spec ID | SPEC-001 |
| Status | Approved — implementation not started |
| Owner | @ReivaxBoido |
| Issue | #1 |
| Plan | [`plan.md`](plan.md) — approved at G2 |
| Tasks | [`tasks.md`](tasks.md) — approved at G2 |
| Target release | v0.1.0 |
| Delivered in | v0.1.0 (implementation complete; container checks pending) |

> **Gate status:** G1 closed; **G2 approved on 2026-10-31**. Implementation may begin under the task
> list. No production code existed at approval time.

## 1. Problem

`telegram-auto-poll` has a ratified constitution but no application code. An operator who wants a
recurring poll in a Telegram group currently has to remember to post it by hand, which is unreliable
and produces inconsistent wording and options.

The first feature must turn the constitution into a running system: a Dockerized bot that an
allowlisted admin can configure over DM, that stores poll definitions and schedules in SQLite, and that
delivers each due poll exactly once to each target chat — including correct behavior across time zones,
DST boundaries, restarts, and Telegram errors.

Without this feature there is nothing to deploy, and every later spec (editing, archiving, resending,
webhooks) has no foundation to build on.

## 2. Goals

- **G-1:** An operator can start the service with `docker compose up -d` and reach a healthy state
  (`/healthz` 200) using only documented environment variables.
- **G-2:** An allowlisted admin can, in a Telegram DM, register a target chat, create a poll, and
  attach a daily/weekly/monthly/one-off schedule without editing files or touching the database.
- **G-3:** A due schedule produces exactly one poll per active target chat per occurrence, even across
  restarts, duplicate scheduler ticks, or clock adjustments.
- **G-4:** An occurrence missed beyond the grace window is recorded as skipped, never sent late, and an
  admin can see that it happened.
- **G-5:** Transient send failures retry with bounded backoff; terminal failures alert admins once and
  are queryable afterwards.
- **G-6:** Every mutation is attributable in an audit log, and no secret appears in logs, messages, or
  the repository.
- **G-7:** The delivered code passes the constitution's quality gates: `ruff`, `mypy --strict`, and
  `pytest` at 100% line and branch coverage, plus security and container checks.

## 3. Non-goals

| ID | Non-goal | Why / when revisited |
|---|---|---|
| NG-1 | Editing an existing poll's fields, duplicating a poll, archiving/unarchiving | Deferred to SPEC-002. SPEC-001 creates and reads polls; it does not mutate definitions. |
| NG-2 | `/resend <delivery_id>` | Deferred to SPEC-002. SPEC-001 alerts on failure and exposes delivery IDs, but recovery is manual `/sendnow`. |
| NG-3 | Adding, removing, or reordering options after creation | Deferred to SPEC-002 with the rest of poll editing. |
| NG-4 | Quiz polls, media polls, poll results, vote analytics | Constitution §1.3. |
| NG-5 | Web UI, REST API, webhook mode | Constitution §1.3 and ADR-0002. |
| NG-6 | Runtime admin management (`/addadmin`) | Constitution §1.3; allowlist is env-only. |
| NG-7 | Internationalization | Constitution §1.3; English only. |
| NG-8 | Automatic deployment from CI | Constitution §1.3; the operator pulls manually. |
| NG-9 | Per-chat schedules, multiple bot tokens, horizontal scaling | Constitution §1.3 and C-1. |

SPEC-001 is a coherent vertical slice, not the whole of v1. v1 is complete only after the deferred P1
requirements (editing, archive, resend) ship in later specs.

## 4. Users and stories

| Persona | Story |
|---|---|
| Admin | As an admin, I want to register a group or channel once and reuse it for many polls, so that I do not paste chat IDs every time. |
| Admin | As an admin, I want a guided wizard that validates the question, description, options, flags, targets, and schedule, so that I cannot create an invalid poll. |
| Admin | As an admin, I want to see the next five occurrences in the schedule's timezone before saving, so that DST and month-end behavior is not a surprise. |
| Admin | As an admin, I want `/status` to show what is scheduled next and what recently failed, so that I can trust the bot without reading logs. |
| Operator | As an operator, I want a single container with health and metrics endpoints, so that I can deploy and monitor it on one VPS. |
| Operator | As an operator, I want startup to recover safely from a crash, so that a restart never duplicates or late-sends polls. |

## 5. Functional requirements

IDs are document-scoped and start at 101 to avoid colliding with the constitution's project-wide
`FR-001`–`FR-026`. The traceability column names the constitution requirement this elaborates.

### 5.1 Configuration and startup

| ID | Requirement | Priority | Acceptance criteria | Traces to |
|---|---|---|---|---|
| FR-101 | The app MUST load configuration from environment variables via `pydantic-settings`, validate every value, and abort startup with a message naming the variable and expected format on any invalid value. | P0 | Given a missing `TELEGRAM_BOT_TOKEN`, when the process starts, then it exits non-zero and logs `config_invalid` naming the variable without printing a secret. | §8.1 |
| FR-102 | The app MUST create/open SQLite at `DATABASE_URL`, apply `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout`, and `synchronous=NORMAL`, and apply all Alembic migrations at startup. | P0 | Given an empty data directory, when the app starts, then all tables and indexes exist and `alembic_version` equals head. | §5, §14.1 |
| FR-103 | The app MUST acquire an exclusive lock on `<DATA_DIR>/app.lock` and exit non-zero if another instance holds it. | P0 | Given a running instance, when a second instance starts against the same `DATA_DIR`, then it exits non-zero and does not poll or send. | §3.3, C-1 |
| FR-104 | Startup MUST recover interrupted deliveries and missed occurrences before scheduling new work: rows left in `sending` become `failed` with `ambiguous_outcome_after_restart`; occurrences older than the grace window become `skipped`; within-grace occurrences remain due. | P0 | Given a `sending` row at startup, when recovery runs, then the row is `failed`, `completed_at` is set, and no send is attempted. | FR-022, §6.5.1 |
| FR-105 | Startup MUST take a `sqlite3` online backup to `<DATA_DIR>/backups/pre-migrate-<timestamp>.db` before migrations and retain the five most recent pre-migration backups. | P1 | Given an existing database, when migrations are pending, then a backup exists before `alembic upgrade head` runs and older backups beyond five are removed after success. | §14.1 |
| FR-126 | The app MUST shut down gracefully on `SIGTERM`/`SIGINT`: stop accepting updates and new work, drain in-flight sends for at most `SHUTDOWN_GRACE_SECONDS`, checkpoint WAL, release the lock, and exit 0. | P0 | Given an in-flight send, when `SIGTERM` arrives, then the send completes or is abandoned within the grace period and the process exits 0. | §3.5 |

### 5.2 Admin access and commands

| ID | Requirement | Priority | Acceptance criteria | Traces to |
|---|---|---|---|---|
| FR-106 | The bot MUST accept commands only in private chats from user IDs in `ADMIN_USER_IDS`; group/channel commands are ignored, and non-allowlisted users receive one generic refusal. | P0 | Given a group message, when it contains `/status`, then the bot sends nothing. Given a non-allowlisted DM, then the reply is exactly the generic private-bot message. | FR-001 |
| FR-107 | Every callback query MUST re-authorize `callback.from_user.id` against the allowlist and reject unknown callback namespaces without mutating state. | P0 | Given a forged callback from a non-allowlisted user, when it is handled, then the answer is a refusal and no audit event or state change occurs. | §6.1, §10.2 |
| FR-108 | Admin commands MUST be rate-limited per user with a token bucket (`ADMIN_COMMANDS_PER_MINUTE`, default 20); `/sendnow` is limited separately to 5 per minute. | P1 | Given 21 commands in a minute, when the 21st arrives, then it is refused with retry-after seconds and produces no mutation. | FR-002 |
| FR-109 | `/start`, `/help`, `/version`, and `/status` MUST be implemented; `/status` reports version, uptime, lock state, DB check, scheduler heartbeat age, counts, next five runs, and last five failures within 2 s p95. | P1 | Given a healthy running instance, when an admin sends `/status`, then the reply contains every listed field and no secret. | FR-018, FR-025 |
| FR-110 | The bot MUST register its command menu only for private chats. | P2 | Given startup, when the bot registers commands, then the scope is `BotCommandScopeAllPrivateChats`. | FR-023 |
| FR-111 | A wizard MUST expire after `WIZARD_TTL_SECONDS` (default 900) of inactivity, support `/cancel`, and never commit state before final confirmation. | P1 | Given a wizard idle beyond TTL, when the next message arrives, then the wizard is discarded and the admin is told to start again. | FR-024 |

### 5.3 Chat registration

| ID | Requirement | Priority | Acceptance criteria | Traces to |
|---|---|---|---|---|
| FR-112 | `/addchat` MUST accept either a forwarded message from the target chat or a pasted negative numeric chat ID, call `getChat`, and accept only `group`, `supergroup`, or `channel`. | P0 | Given a forwarded channel message, when `/addchat` processes it, then the chat is stored with type `channel` and its title. Given a private chat or bot, then it is rejected with an explanation. | FR-003 |
| FR-113 | For channels the bot MUST verify administrator status with `can_post_messages`; for groups it MUST warn when send permission is missing but still store the chat. | P1 | Given a channel where the bot is not an admin, when registering, then registration fails with the required action. | FR-003 |
| FR-114 | Registration MUST be idempotent by `telegram_chat_id` and MUST reactivate a deactivated chat. | P1 | Given an existing inactive chat, when it is registered again, then `is_active` becomes 1 and no duplicate row exists. | FR-003 |
| FR-115 | `/chats` MUST list registered chats with pagination (8 per page), and `/removechat <id>` MUST deactivate after confirmation without deleting history; pending deliveries for that chat become `skipped` with reason `chat_inactive`. | P1 | Given an active chat with a pending delivery, when it is removed, then the chat is inactive, the delivery is `skipped`, and the row is retained. | FR-004 |

### 5.4 Poll creation and viewing

| ID | Requirement | Priority | Acceptance criteria | Traces to |
|---|---|---|---|---|
| FR-116 | `/newpoll` MUST run a guided wizard collecting question (1–300), optional description (0–1024), 2–12 unique options (1–100 each), anonymity (default on), multiple answers (default off), at least one active target, and optionally one schedule. | P0 | Given a valid wizard run, when the admin confirms, then exactly one poll, its targets, and its schedule (if chosen) are committed in one transaction and audit events are written. | FR-005, I-1, I-2 |
| FR-117 | Invalid input MUST re-prompt with the specific constraint and the current length, preserving prior answers. | P1 | Given a 301-character question, when submitted, then the bot replies with the 300 limit and the current length and stays on the question step. | §6.4 |
| FR-118 | The wizard MUST show a preview of the exact poll payload and the next five occurrences before committing. | P1 | Given a weekly schedule, when the preview is shown, then five ordered local datetimes with UTC offsets are displayed. | FR-011 |
| FR-119 | `/polls` MUST paginate poll summaries (8 per page) and `/poll <id>` MUST show the definition, targets, schedules, and next runs. | P1 | Given 9 polls, when `/polls` is sent, then page 1 shows 8 and page 2 shows 1. | FR-008 |
| FR-120 | Poll content MUST be sent as plain text; no parse mode is used, and no user-supplied text is interpolated into HTML. | P0 | Given a question containing `<b>`, when sent, then Telegram receives the literal characters. | §10.2 |

### 5.5 Schedules

| ID | Requirement | Priority | Acceptance criteria | Traces to |
|---|---|---|---|---|
| FR-121 | An admin MUST be able to create daily, weekly, monthly, and one-off schedules for a poll with an IANA timezone and optional start/end local dates. | P0 | Given each kind, when created, then `next_run_at_utc` is computed correctly and the row validates against I-3–I-8. | FR-009 |
| FR-122 | Weekly schedules MUST accept 1–7 distinct ISO weekdays; monthly MUST accept 1–31 and warn above 28; one-off MUST reject a past instant. | P0 | Given day 0 or 32, when submitted, then the wizard rejects with the valid range. Given a past one-off, then it is rejected. | I-4, I-5 |
| FR-123 | `/schedules` and `/schedule <id>` MUST show kind, timezone, bounds, pause state, last run, and the next five occurrences. | P1 | Given an active schedule, when inspected, then the next five occurrences match the pure schedule-math function. | FR-011 |
| FR-124 | `/pause` and `/resume` MUST be implemented; pausing sets `next_run_at_utc` null and resuming recomputes it from now. `/delete_schedule <id>` MUST require confirmation and hard-delete only the schedule. | P0 | Given a paused schedule, when resumed, then `next_run_at_utc` is in the future and greater than `created_at`. | FR-010, I-7, I-8 |

### 5.6 Delivery

| ID | Requirement | Priority | Acceptance criteria | Traces to |
|---|---|---|---|---|
| FR-125 | The scheduler MUST evaluate due schedules at least every `SCHEDULER_TICK_SECONDS`, create one `deliveries` row per active target per occurrence in one transaction, and advance `next_run_at_utc`. | P0 | Given a due schedule with 2 targets, when the tick runs, then exactly 2 `pending` rows exist and `next_run_at_utc` advanced. | FR-012, FR-013 |
| FR-127 | Delivery creation MUST be idempotent via the partial unique index on `(schedule_id, scheduled_for_utc, chat_id)`; a conflicting insert is ignored, never duplicated or errored. | P0 | Given a repeated tick for the same occurrence, when it runs, then the row count is unchanged and no exception escapes. | I-9, §6.5.1 |
| FR-128 | An occurrence older than `SEND_GRACE_SECONDS` MUST create `skipped` rows with reason `missed_grace_window` and advance the schedule; it MUST NOT be sent. | P0 | Given a 121-second-late occurrence, when evaluated, then rows are `skipped` and no `sendPoll` call occurs. | FR-014 |
| FR-129 | The delivery worker MUST claim `pending` rows, re-check that poll and chat are active, and call `sendPoll` with `type=regular`, question, description (when set), options, `is_anonymous`, and `allows_multiple_answers`. | P0 | Given a pending row, when the worker sends, then the fake sender records exactly those fields and the row becomes `sent` with a message ID. | FR-012 |
| FR-130 | Sending MUST respect per-chat (1/s), per-group (20/min), and global (25/s) limits with bounded concurrency `SEND_CONCURRENCY`; a `429` MUST honor `retry_after` clamped to 300 s. | P1 | Given a fake clock and 3 sends to one chat, when processed, then sends are spaced by at least 1 s of fake time. Given `429 retry_after=7`, then the next attempt is at least 7 s later. | §6.5 |
| FR-131 | Retryable failures MUST retry up to `SEND_RETRY_ATTEMPTS` with exponential backoff and ±20% jitter; non-retryable failures MUST fail immediately; terminal failures MUST alert all admins exactly once per delivery row. | P0 | Given two timeouts then success, when retried, then one `sent` row with `attempt_count=3` and no alert. Given `chat not found`, then `failed` with one alert. | FR-015 |
| FR-132 | `/sendnow <poll_id>` MUST send a manual delivery to all active targets after confirmation, with `trigger=manual`, and MUST NOT collide with the scheduled uniqueness constraint. | P1 | Given a poll with a scheduled delivery for the same chat, when `/sendnow` is confirmed, then a second row with `trigger=manual` is created and sent. | FR-016 |
| FR-133 | A delivery in `sent` status MUST have `telegram_message_id` and `completed_at`; a `failed`/`skipped` row MUST have `completed_at`; `last_error` MUST be sanitized of tokens and secrets. | P0 | Given a failed send, when persisted, then `last_error` contains the classification and no token substring. | I-10, I-11, §10.2 |

### 5.7 Audit, retention, and observability

| ID | Requirement | Priority | Acceptance criteria | Traces to |
|---|---|---|---|---|
| FR-134 | Every mutation MUST write an immutable `audit_events` row in the same transaction, recording actor user ID, action verb, entity type/ID, and non-secret details. | P1 | Given a poll creation, when committed, then one audit row exists with `action='poll.create'` and the actor ID. A rolled-back transaction leaves no audit row. | FR-019 |
| FR-135 | A daily job at 03:15 UTC MUST delete `deliveries` and `audit_events` older than `RETENTION_DAYS` in batches of 1,000 and checkpoint the WAL; it MUST be idempotent and logged. | P1 | Given 2,500 old deliveries, when the job runs, then all are deleted in 3 batches and a second run deletes 0. | FR-020 |
| FR-136 | The process MUST serve `/livez`, `/healthz`, and `/metrics` on the configured health interface; `/healthz` returns 503 with reasons when the DB check or scheduler heartbeat fails. | P1 | Given a stalled scheduler, when `/healthz` is probed, then it returns 503 and names the scheduler reason. | FR-021, §16.2 |
| FR-137 | Logs MUST be one JSON object per line with `timestamp`, `level`, `logger`, `message`, `service`, `version`, `environment`, and correlation fields where applicable; tokens and raw updates MUST be redacted. | P1 | Given a startup and a send, when logs are captured, then every line parses as JSON and a known fake token never appears. | §16.1 |
| FR-138 | A Docker image and Compose service MUST be delivered: multi-stage build, non-root UID/GID 10001, read-only root FS, `cap_drop: ALL`, `no-new-privileges`, `/data` volume, loopback-only published health port, and a healthcheck. | P0 | Given `docker compose up -d`, when the image builds and starts, then the container reports healthy and `/version` matches the build args. | §15.1, §15.2 |

### 5.8 Deferred requirements (tracked, not in SPEC-001)

| Constitution requirement | Deferred to | Reason |
|---|---|---|
| FR-006 (edit any field, duplicate, preview) | SPEC-002 | Preview is in SPEC-001 (FR-118); mutation of an existing poll is not. |
| FR-007 (archive/unarchive) | SPEC-002 | Depends on the edit surface. |
| FR-017 (`/resend`) | SPEC-002 | Manual `/sendnow` covers recovery in the interim. |
| FR-006 option add/remove/reorder | SPEC-002 | Same edit surface. |

## 6. Non-functional requirements

| ID | Requirement | Target | Verification |
|---|---|---|---|
| NFR-101 | Coverage | 100% line and branch of `src/telegram_auto_poll`, excluding only the four cases allowed by constitution §11.3 | `pytest --cov --cov-branch --cov-fail-under=100` |
| NFR-102 | Startup time | ≤ 15 s on the reference dataset, excluding image pull | integration test with a seeded DB + manual check |
| NFR-103 | Delivery latency | p99 ≤ 30 s after the occurrence, always inside the 120 s grace window | `tap_schedule_lag_seconds` + fake-clock tests |
| NFR-104 | Duplicate scheduled sends | 0 per release | unique-index integration test + reconciliation at G4 |
| NFR-105 | Test isolation | No network, no real clock, no sleeps in unit/integration tests | review + a socket-blocking fixture |
| NFR-106 | Admin latency | `/status` p95 ≤ 2 s excluding Telegram network | integration timing test with a seeded DB |
| NFR-107 | Image size | ≤ 250 MB compressed | CI `docker image inspect` check |
| NFR-108 | Resource envelope | ≤ 256 MB RSS at reference load | manual measurement recorded in the PR |

## 7. Data model changes

Initial schema, created by one Alembic migration (`0001_initial`):

| Table | Purpose | Key constraints |
|---|---|---|
| `chats` | Registered groups/supergroups/channels | `UNIQUE(telegram_chat_id)`, `type IN ('group','supergroup','channel')` |
| `polls` | Reusable poll definitions | `CHECK(length(question) BETWEEN 1 AND 300)`, `CHECK(json_valid(options_json))` |
| `poll_targets` | Poll ↔ chat association | Composite PK `(poll_id, chat_id)`, FKs `ON DELETE RESTRICT` |
| `schedules` | Recurrence rules and next run | `CHECK(kind IN (...))`, index `(is_paused, next_run_at_utc)` |
| `deliveries` | One row per poll/chat/occurrence attempt-series | Partial unique index `(schedule_id, scheduled_for_utc, chat_id) WHERE schedule_id IS NOT NULL`; index `(status, next_attempt_at_utc)` |
| `audit_events` | Immutable mutation log | Index `(created_at)`, `(entity_type, entity_id)` |

- **Migration strategy:** single initial migration; no backfill. Alembic batch mode with a metadata
  naming convention so future SQLite `ALTER TABLE` operations are deterministic.
- **Retention impact:** `deliveries` and `audit_events` are pruned after 90 days (FR-135). Definition
  tables are never pruned.
- **Invariants affected:** I-1 through I-15 are created by this feature; the migration must include the
  checks that can be expressed in SQLite, and the rest are enforced in domain code and tests.
- **Backup:** pre-migration backup per FR-105.

## 8. Telegram UX

### 8.1 Commands in scope

`/start`, `/help`, `/version`, `/status`, `/chats`, `/addchat`, `/removechat`, `/polls`, `/poll`,
`/newpoll`, `/schedules`, `/schedule`, `/pause`, `/resume`, `/delete_schedule`, `/sendnow`, `/cancel`.

Not in scope: `/resend`, `/archive`, `/unarchive` (SPEC-002).

### 8.2 Wizard steps (new poll)

1. Question — validate 1–300, show remaining characters.
2. Description — validate 0–1024, or **Skip**.
3. Options — add each; list with **Add another** / **Done**; enforce 2–12, 1–100, uniqueness.
4. Anonymity — toggle, default **Anonymous**.
5. Multiple answers — toggle, default **Single answer**.
6. Targets — multi-select active chats, at least one.
7. Schedule — Daily / Weekly / Monthly / One-off / **Save without schedule**; timezone defaults to
   `DEFAULT_TIMEZONE`; optional start/end dates; next-five preview.
8. Preview — exact payload and schedule.
9. Confirm — one transaction, audit events, show IDs.

Every step supports **Back**, **Cancel**, and re-prompts on invalid input without losing prior answers.

### 8.3 Callback data

Versioned compact scheme `v1:<namespace>:<action>:<id>`, maximum 64 bytes, for example
`v1:p:view:12`, `v1:s:pause:34`, `v1:w:opt:done`. Handlers validate the actor, the version, and the
namespace before any mutation.

### 8.4 Pagination and limits

- Lists show 8 items per page; buttons carry page and filter.
- Admin messages stay under 4,096 characters; long content is summarized or paged.
- Inline keyboards: at most 8 rows, at most 4 buttons per row.
- Callback answers are sent within 3 seconds; long work is acknowledged first.

### 8.5 Empty, error, and confirmation states

- Empty lists explain the creating command (`/newpoll`, `/addchat`).
- Permission errors state the required action (for example, “Add the bot as a channel administrator
  with ‘Post messages’”).
- Destructive actions (`/removechat`, `/delete_schedule`, `/sendnow`) show a summary and a two-button
  confirmation with the entity ID.
- Unexpected errors show a correlation ID matching the structured log entry and never a stack trace.

## 9. Edge cases and failure modes

| Case | Expected behavior | Test |
|---|---|---|
| DST spring-forward, local time does not exist | Shift forward to the first valid instant; send once | `test_schedule_math.py::test_spring_forward_nonexistent_time_shifts_forward` |
| DST fall-back, local time is ambiguous | Use `fold=0`; send once, not twice | `test_schedule_math.py::test_fall_back_ambiguous_time_sends_once` |
| Monthly on day 31 in a 30-day month | Skip that month; do not clamp | `test_schedule_math.py::test_monthly_missing_day_is_skipped` |
| Monthly on day 29 in February, leap and non-leap | Send only in leap years | `test_schedule_math.py::test_monthly_feb_29` |
| One-off instant in the past | Reject at creation and update | `test_validation.py::test_once_past_rejected` |
| Occurrence exactly at the grace boundary | Sent when `now - occurrence <= grace`, skipped when greater | `test_delivery_service.py::test_grace_boundary` |
| Occurrence just beyond grace | `skipped`, reason `missed_grace_window`, no send | `test_scheduler.py::test_missed_occurrence_skipped` |
| Crash after request, before commit | Row stays `sending`; startup marks `failed`, no auto-retry | `test_recovery.py::test_sending_row_fails_on_restart` |
| Telegram 429 with `retry_after` | Retry at `max(backoff, retry_after)`, capped at 300 s | `test_sender.py::test_429_honors_retry_after` |
| Telegram 5xx / timeout | Retry with backoff until attempts exhausted | `test_sender.py::test_retryable_errors_retry` |
| Bot kicked / chat not found | `failed` immediately, one admin alert | `test_sender.py::test_non_retryable_fails_fast` |
| Channel without `can_post_messages` | Registration rejected with required action | `test_chat_service.py::test_channel_requires_post_permission` |
| Last active target removed | Poll's schedules pause; admin warned | `test_chat_service.py::test_last_target_removal_pauses_schedules` |
| Inactive chat at send time | Delivery `skipped`, reason `chat_inactive` | `test_delivery_service.py::test_inactive_chat_skipped` |
| Paused schedule | No delivery rows created; `next_run_at_utc` null | `test_scheduler.py::test_paused_schedule_not_evaluated` |
| Two scheduler ticks for the same occurrence | One row per target; second tick is a no-op | `test_scheduler.py::test_duplicate_tick_idempotent` |
| Non-allowlisted callback | Refused; no state change, no audit row | `test_auth.py::test_callback_requires_allowlist` |
| Command in a group | Ignored entirely, no reply | `test_auth.py::test_group_command_ignored` |
| Callback data over 64 bytes | Cannot be constructed; codec test asserts every generated key | `test_callbacks.py::test_callback_keys_under_64_bytes` |
| Second instance on the same data dir | Exits non-zero, does not poll | `test_lock.py::test_second_instance_refused` |
| Invalid timezone string | Rejected by validation with the IANA requirement | `test_validation.py::test_invalid_timezone_rejected` |

## 10. Security and privacy

- **Authorization:** every message handler and callback handler checks private chat + allowlist
  (FR-106, FR-107). No handler trusts wizard state as identity.
- **Input validation:** all poll text is length- and uniqueness-validated at the domain boundary; chat
  IDs must exist in `chats` before a schedule or manual send targets them.
- **Secrets:** the bot token is read from the environment, never logged, and redacted centrally. Tests
  assert a known fake token never appears in captured logs, Telegram calls, or `last_error`.
- **Data stored:** chat metadata, poll definitions, schedules, delivery status/message IDs, audit
  events. No message bodies, voter identities, or raw updates.
- **Data not collected:** the bot never calls `getPollResults` or `stopPoll`.
- **Audit:** every mutation records actor, action, entity, and non-secret details (FR-134).
- **Threat-model changes:** none beyond constitution §10.1; this feature implements the existing model.
- **Plain text:** poll content is never parsed as HTML (FR-120).

## 11. Observability

- **Log events (stable `message` values):** `config_loaded`, `config_invalid`, `startup_complete`,
  `startup_recovery`, `migration_applied`, `poll_created`, `schedule_created`, `schedule_paused`,
  `schedule_resumed`, `schedule_deleted`, `chat_registered`, `chat_deactivated`, `delivery_created`,
  `delivery_sent`, `delivery_failed`, `delivery_skipped`, `retention_completed`, `shutdown_complete`,
  `auth_denied`.
- **Metrics (constitution §16.3):** `tap_build_info`, `tap_startup_timestamp_seconds`,
  `tap_scheduler_heartbeat_age_seconds`, `tap_scheduler_tick_duration_seconds`,
  `tap_schedules_active`, `tap_deliveries_pending`, `tap_polls_sent_total`,
  `tap_send_failures_total`, `tap_send_duration_seconds`, `tap_schedule_lag_seconds`,
  `tap_admin_commands_total`, `tap_retention_deleted_rows_total`, `tap_db_up`, `tap_bot_polling_up`.
- **Health:** `/livez` process-only; `/healthz` requires a DB `SELECT 1` ≤ 1 s and a scheduler heartbeat
  younger than 3 ticks.
- **Admin alerts:** one alert per terminal delivery failure; one startup summary when occurrences were
  skipped.
- **SLO impact:** this feature establishes the p99 ≤ 30 s timeliness and zero-duplicate SLOs.

## 12. Test plan

### 12.1 Unit

- Schedule math for every kind across DST spring/fall, month boundaries, leap day, year boundary,
  start/end inclusivity, and `next_run_at_utc` monotonicity.
- Validation boundaries: 0/1/max/max+1 for question, description, option; 1/2/12/13 options; duplicate
  and blank options; invalid timezone, weekday, day-of-month, past one-off.
- Retry classification and backoff computation with a seeded jitter source.
- Callback codec: round-trip, version rejection, ≤ 64 bytes for every generated key.
- Wizard state machine: every transition, cancel, timeout, invalid re-prompt.
- Config parsing: missing token, empty allowlist, bad port, invalid timezone.

### 12.2 Integration

- Repositories against a temporary SQLite file with migrations applied; uniqueness and FK enforcement.
- Scheduler tick creates the expected rows and advances `next_run_at_utc`.
- Delivery worker with a `FakeSender` and `FakeClock`: success, retry, exhaustion, 429, non-retryable,
  inactive chat, archived poll.
- Startup recovery: `sending` → `failed`; stale occurrence → `skipped`; within-grace stays due.
- `/livez`, `/healthz` 200/503, `/metrics` exposition via an aiohttp test client.
- Single-instance lock with a second process/`flock` attempt.

### 12.3 End-to-end / manual

- `docker compose up -d` against a dedicated test bot token and test group.
- Register the chat, create a one-off poll two minutes ahead, confirm exactly one delivery and correct
  content.
- Restart the container during the window and confirm no duplicate and no late send beyond grace.

### 12.4 Coverage and quality

- 100% line and branch on `src/telegram_auto_poll`, exclusions limited to constitution §11.3.
- No network in unit/integration tests, enforced by a fixture that blocks sockets.
- `ruff format --check`, `ruff check`, `mypy --strict` clean.

## 13. Rollout and rollback

- **Deployment:** merge, tag `v0.1.0`, CI publishes the GHCR image, operator runs
  `docker compose pull && docker compose up -d`.
- **Migration:** initial migration only; pre-migration backup is taken automatically (FR-105).
- **Rollback:** redeploy the previous image. There is no previous schema in the first release, so a
  rollback before data exists is a stop-and-remove; after data exists, rollback means restoring the
  pre-migration backup.
- **Data-loss risk:** none on the happy path; a failed first migration leaves the backup intact.
- **First-run checklist:** `.env` with token and admin IDs, admin presses Start, `/addchat`,
  `/newpoll`, verify `/status`, then `/sendnow` to a staging chat.

## 14. Clarifications (G1)

| # | Question | Answer | Answered by | Date |
|---|---|---|---|---|
| Q-1 | Should SPEC-001 be one end-to-end vertical slice (config → DB → admin UX → scheduler → sender → Docker) or two specs (foundation, then admin UX)? | One end-to-end vertical slice. Editing, duplicate, archive/unarchive, and `/resend` are deferred to SPEC-002 (§5.8, §3). | @ReivaxBoido | 2026-10-31 |
| Q-2 | May I create the GitHub issue for this spec in the public repository now (sanitized, no secrets/chat IDs), or should the spec keep `Issue: #TBD` until later? | Create it now, sanitized. Issue **#1**. | @ReivaxBoido | 2026-10-31 |
| Q-3 | For the manual end-to-end check at G4, do you have a dedicated test bot token and test group/channel, or should G4 rely on automated tests only and defer the live check? | A dedicated test bot token and a test group/channel are available. The live check runs at G4; the token stays in a local `.env` and is never committed. | @ReivaxBoido | 2026-10-31 |

The following assumptions were confirmed implicitly by the scope answer and do not need a further
question:

- The deferred items in §5.8 are acceptable for the first release.
- `DEFAULT_TIMEZONE` stays `UTC` unless the operator overrides it.
- Admin onboarding uses a numeric user ID obtained outside the bot (for example, `@userinfobot`).

**G1 approval:** answered by @ReivaxBoido on 2026-10-31 through structured Q&A (Q-1–Q-3). The formal
`GATE G1 APPROVED` comment is recorded on issue #1.

## 15. Change log

| Date | Change | Reason | Approved by |
|---|---|---|---|
| 2026-10-31 | Initial draft created from the ratified constitution. | Start of SPEC-001. | — |
| 2026-10-31 | G1 answers recorded (Q-1–Q-3); status moved to `Clarified`; issue #1 linked. | Clarifications closed. | @ReivaxBoido |
| 2026-10-31 | Status moved to `Approved`; G2 recorded. No scope change. | Spec, plan, and tasks approved before implementation. | @ReivaxBoido |

## 16. Gate record

| Gate | Artifact | Approved by | Date | Comment link |
|---|---|---|---|---|
| G1 | Clarifications (§14) | @ReivaxBoido | 2026-10-31 | issue #1, comment transcribed from structured Q&A |
| G2 | `spec.md`, `plan.md`, `tasks.md` | @ReivaxBoido | 2026-10-31 | issue #1, G2 record comment |
| G3 | Working tree, `traceability.md`, local gates, container build | @ReivaxBoido | 2026-10-31 | issue #1 comment 6536596064 (request); approval message: "approve" |
| G4 | Reconciliation | | | |
