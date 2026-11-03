# Plan: SPEC-003 — CountMeIn core

| Field | Value |
|---|---|
| Spec | [`spec.md`](spec.md) |
| Status | Approved — G2 approved 2026-11-02 |
| Author | @ReivaxBoido |
| ADRs | ADR-0003 (product pivot), ADR-0004 (participant callback authorization) |
| Constitution | Proposed amendment v2.0.0 (`constitution-amendment.md`) |

## 1. Approach

SPEC-003 replaces the native-poll pipeline with a sign-up poll pipeline. The change is vertical:
domain validation and rendering, persistence, services, Telegram UX, the posting worker, retention,
metrics, and docs.

The core model separates four concepts that the native-poll model conflated:

1. **Poll** — a reusable definition: title and ordered options.
2. **Instance** — one publication of that poll (manual in SPEC-003; scheduled in SPEC-004). Responses
   belong to the instance, so each occurrence starts fresh.
3. **Message** — one bot message per instance/chat. It carries the Telegram message ID and the
   delivery state. A single instance can have several messages, one per target chat.
4. **Response** — a user's membership in one option of one instance. A user can hold several options
   (multi-select); toggling again removes the row.

Control flow:

- **Create:** DM wizard → `PollService.create` validates and writes `polls` + `poll_options` +
  `poll.create` audit in one transaction. The poll is `draft`.
- **Publish:** `/publish` → target selection → `PublishService.publish` writes one `poll_instances`
  row and one `poll_messages` row per selected chat, plus `poll.publish` audit, in one transaction.
  No Telegram call is made inside the transaction. The posting worker is woken.
- **Post:** `PostingWorker` claims pending messages, re-checks state, calls
  `MessageGateway.send_message`, and records `sent`/`failed`/`skipped`.
- **Vote:** callback → `VoteGuard` validates the callback against the stored message and instance →
  `ResponseService.toggle` inserts/deletes the response and marks the instance dirty → the
  `ResponseBroadcaster` coalesces the dirty mark, renders once, and edits every sent message for the
  instance with bounded concurrency.
- **Close/reopen/delete:** `PollService` mutates the instance/poll state, updates message rows, and
  asks the broadcaster to re-render or redact. Pending messages are skipped on close/delete.
- **Retention:** the daily job redacts messages and prunes responses for expired closed/deleted
  instances, then prunes old message/audit rows.

Transaction boundaries live in services. Repositories accept an `AsyncSession` and never commit.
Telegram calls never run inside a transaction. The broadcaster and posting worker are supervised
asyncio tasks owned by the composition root.

## 2. Architecture impact

### 2.1 Modules added or changed

| Area | Change |
|---|---|
| `domain/entities.py` | Replace `Poll` with `Poll`, `PollOption`, `PollInstance`, `PollMessage`, `PollResponse`; keep `Chat`, `AuditEvent`. |
| `domain/enums.py` | Add `PollStatus`, `InstanceStatus`, `MessageStatus`, `InstanceTrigger`; remove native `ScheduleKind` until SPEC-004. |
| `domain/validation.py` | Keep timezone/chat validation; add poll title/option/name validation. |
| `domain/poll_render.py` | New pure renderer: message text, vote keyboard model, poll detail text, response list text. |
| `domain/errors.py` | Add `PollClosedError`, `ResponseRejectedError`, `MessageEditError(retryable, retry_after)`, `DestructiveMigrationError`. |
| `domain/ports.py` | Add `MessageGateway`, `PollRepository`, `InstanceRepository`, `MessageRepository`, `ResponseRepository`; keep `Clock`, `ChatGateway`, `AdminNotifier`. |
| `db/models.py` | Replace `PollRow`, `PollTargetRow`, `ScheduleRow`, `DeliveryRow` with `PollRow`, `PollOptionRow`, `PollInstanceRow`, `PollMessageRow`, `PollResponseRow`; keep `ChatRow`, `AuditEventRow`. |
| `db/repositories/` | Add `poll_options.py`, `poll_instances.py`, `poll_messages.py`, `poll_responses.py`; replace `polls.py`; remove `schedules.py`, `deliveries.py` until SPEC-004. |
| `db/migrations/versions/0002_countmein_core.py` | New destructive-reset migration with guard. |
| `services/poll_service.py` | Rewrite for create/read/close/reopen/delete/refresh. |
| `services/publish_service.py` | New: target validation, instance + message creation, audit. |
| `services/response_service.py` | New: toggle, validation, rate limit, dirty marking. |
| `services/broadcaster.py` | New: per-instance debounce, render, bounded edits, retries, shutdown flush. |
| `services/posting_worker.py` | New: claim pending messages, send, retry, skip, alert. |
| `services/retention_service.py` | Adapt: response retention and message redaction. |
| `services/recovery_service.py` | Adapt: `sending` → `failed`; no native delivery recovery. |
| `services/audit_service.py` | Keep; widen entity types. |
| `services/chat_service.py` | Keep. |
| `services/status_service.py` (or existing status code) | Adapt counts. |
| `bot/auth.py` | Split `authorize_admin_message`, `authorize_admin_callback`, `authorize_vote_callback`; add vote bucket. |
| `bot/callbacks.py` | New codec: `v1:v`, `v1:p`, `v1:t`, `v1:w`, `v1:c`. |
| `bot/keyboards.py` | Poll detail, target selection, vote keyboard, confirmations. |
| `bot/render.py` | Poll detail, publish confirmation, response list, status. |
| `bot/wizards/` | Replace native poll wizard with sign-up poll wizard; keep the generic store/engine patterns. |
| `bot/handlers/commands.py` | New command set; remove schedule/sendnow/resend handlers. |
| `bot/telegram_gateway.py` | Add `send_message`, `edit_message_text`; keep `get_chat`, `get_chat_member`. |
| `bot/telegram_sender.py` | Delete (`sendPoll` is gone). |
| `scheduler/engine.py` | Replace with a heartbeat-only scheduler; remove `planner.py` and `worker.py` until SPEC-004. |
| `observability/metrics.py` | New poll/vote/edit metrics; remove native schedule/delivery metrics. |
| `app.py` | Wire new services, worker, broadcaster, retention, heartbeat. |
| `config.py` | Add the eight new settings; keep existing settings needed by SPEC-004. |

### 2.2 Dependency direction

Unchanged: `bot → services → domain`, `services → db` repositories, `scheduler`/workers → services,
`app` → everything (composition root only). `domain/` imports only the standard library.

### 2.3 New ports and adapters

| Port | Adapter | Fake |
|---|---|---|
| `MessageGateway` | `TelegramMessageGateway` (`send_message`, `edit_message_text`, `answer_callback`) | `FakeMessageGateway` records calls, can fail by error class |
| `PollRepository` | SQLAlchemy repository | Temporary SQLite |
| `InstanceRepository` | SQLAlchemy repository | Temporary SQLite |
| `MessageRepository` | SQLAlchemy repository | Temporary SQLite |
| `ResponseRepository` | SQLAlchemy repository | Temporary SQLite |

### 2.4 Background tasks and supervision

- `PostingWorker.run()` — a loop with `POSTING_TICK_SECONDS`; claims and sends pending messages.
  Supervised by `asyncio.TaskGroup`; cancellation drains the current send.
- `ResponseBroadcaster.run()` — consumes dirty instance IDs, debounces, renders, edits. Supervised;
  shutdown flushes for `SHUTDOWN_GRACE_SECONDS`.
- `SchedulerHeartbeat.run()` — updates the heartbeat used by `/healthz`. No due-schedule evaluation
  until SPEC-004.
- Retention job — APScheduler cron at 03:15 UTC, as today.

Every task has a name, an owner, and a cancellation path. The top-level supervisor logs and restarts a
crashed task only if the failure is transient; otherwise the process exits non-zero so Docker restarts
it.

### 2.5 Configuration changes

New settings (defaults in parentheses):

| Variable | Default | Validation |
|---|---|---|
| `RESPONSE_NAMES_PER_OPTION` | 50 | 1–50 |
| `RESPONSE_UPDATE_DEBOUNCE_SECONDS` | 1.0 | 0–10; 0 means edit immediately |
| `VOTES_PER_MINUTE` | 30 | 1–120 |
| `RESPONSE_RETENTION_DAYS` | 90 | 1–3650 |
| `MESSAGE_EDIT_CONCURRENCY` | 4 | 1–20 |
| `MESSAGE_EDIT_RETRY_ATTEMPTS` | 3 | 1–10 |
| `POSTING_TICK_SECONDS` | 5 | 1–60 |
| `ALLOW_DESTRUCTIVE_MIGRATION` | false | boolean; one-time migration guard |

Existing settings used by this spec: `TELEGRAM_BOT_TOKEN`, `ADMIN_USER_IDS`, `DATABASE_URL`,
`DATA_DIR`, `DEFAULT_TIMEZONE`, `ENVIRONMENT`, `LOG_LEVEL`, `LOG_FORMAT`, `HEALTH_HOST`,
`HEALTH_PORT`, `METRICS_ENABLED`, `SEND_GRACE_SECONDS` (reserved for SPEC-004), `SEND_RETRY_ATTEMPTS`,
`SEND_RETRY_BASE_SECONDS`, `SEND_CONCURRENCY`, `ADMIN_COMMANDS_PER_MINUTE`, `RETENTION_DAYS`,
`DB_BUSY_TIMEOUT_MS`, `SHUTDOWN_GRACE_SECONDS`, `MIGRATE_ON_STARTUP`,
`CLOCK_SKEW_WARNING_SECONDS`, `BOT_POLL_TIMEOUT_SECONDS`, `WIZARD_TTL_SECONDS`, `BUILD_VERSION`,
`BUILD_SHA`.

## 3. Data design

### 3.1 Schema

```sql
polls (
  id INTEGER PK,
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
  status TEXT NOT NULL CHECK (status IN ('draft','active','deleted')),
  created_by BIGINT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX ix_polls_status_created_at ON polls(status, created_at);

poll_options (
  id INTEGER PK,
  poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE RESTRICT,
  position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 9),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 100),
  label_folded TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE (poll_id, position),
  UNIQUE (poll_id, label_folded)
);

poll_instances (
  id INTEGER PK,
  poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE RESTRICT,
  status TEXT NOT NULL CHECK (status IN ('open','closed','deleted')),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','scheduled')),
  scheduled_for_utc INTEGER NULL,
  created_by BIGINT NOT NULL,
  created_at INTEGER NOT NULL,
  closed_at INTEGER NULL
);
CREATE INDEX ix_poll_instances_poll_created ON poll_instances(poll_id, created_at);
CREATE INDEX ix_poll_instances_status ON poll_instances(status);

poll_messages (
  id INTEGER PK,
  instance_id INTEGER NOT NULL REFERENCES poll_instances(id) ON DELETE RESTRICT,
  chat_id INTEGER NULL REFERENCES chats(id) ON DELETE RESTRICT,
  inline_message_id TEXT NULL,
  telegram_message_id BIGINT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','sending','sent','failed','missing','skipped')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  next_attempt_at_utc INTEGER NULL,
  last_error TEXT NULL,
  skip_reason TEXT NULL,
  created_at INTEGER NOT NULL,
  sent_at INTEGER NULL,
  updated_at INTEGER NOT NULL,
  CHECK (chat_id IS NOT NULL OR inline_message_id IS NOT NULL)
);
CREATE UNIQUE INDEX ux_poll_messages_instance_chat
  ON poll_messages(instance_id, chat_id) WHERE chat_id IS NOT NULL;
CREATE UNIQUE INDEX ux_poll_messages_instance_inline
  ON poll_messages(instance_id, inline_message_id) WHERE inline_message_id IS NOT NULL;
CREATE INDEX ix_poll_messages_pending ON poll_messages(status, next_attempt_at_utc);

poll_responses (
  id INTEGER PK,
  instance_id INTEGER NOT NULL REFERENCES poll_instances(id) ON DELETE CASCADE,
  option_id INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
  telegram_user_id BIGINT NOT NULL,
  first_name TEXT NOT NULL CHECK (length(first_name) BETWEEN 1 AND 64),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (instance_id, option_id, telegram_user_id)
);
CREATE INDEX ix_poll_responses_instance_option ON poll_responses(instance_id, option_id);
```

`chats` and `audit_events` keep their shape; `audit_events.entity_type` is widened to include
`poll_instance`. The old `polls`, `poll_targets`, `schedules`, and `deliveries` tables are dropped.

### 3.2 Migration steps

1. Acquire the single-instance lock and open the database (existing startup order).
2. Take the pre-migration online backup (existing `migrate.py`).
3. Inspect the old tables. If any of `polls`, `poll_targets`, `schedules`, `deliveries` has rows and
   `ALLOW_DESTRUCTIVE_MIGRATION` is false, raise `DestructiveMigrationError` with the row counts and
   the required flag; the process exits non-zero. The backup remains.
4. Drop `deliveries`, `poll_targets`, `schedules`, `polls` in FK-safe order.
5. Create the new tables and indexes.
6. Rebuild `audit_events` with the widened `entity_type` check using Alembic batch mode.
7. Set `alembic_version` to `0002_countmein_core`.

No backfill. The migration is tested on an empty DB and a 0001 DB seeded with one row per old table.

### 3.3 Query patterns

- Poll list: `SELECT ... FROM polls WHERE status != 'deleted' ORDER BY created_at DESC LIMIT/OFFSET`.
- Latest instance: `SELECT ... FROM poll_instances WHERE poll_id = ? ORDER BY created_at DESC, id DESC LIMIT 1`.
- Open instance for close: `... WHERE poll_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 1`.
- Responses for an instance: `SELECT ... FROM poll_responses WHERE instance_id = ? ORDER BY option_id, created_at, id`.
- Pending messages: `... WHERE status = 'pending' AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc <= ?) ORDER BY id LIMIT ?`.
- Message by instance/chat: use the partial unique index.
- Response toggle: `SELECT` by unique key, then `INSERT`/`DELETE`; the unique index collapses races.

### 3.4 Retention and privacy

- Open instances: responses are never pruned.
- Closed/deleted instances older than `RESPONSE_RETENTION_DAYS`: for each sent message, attempt to edit
  it to the redacted closed/deleted rendering (no names); then delete the responses in batches. If the
  edit fails, log and continue; the Telegram copy may still show names, which is documented.
- `poll_messages` and `audit_events` older than `RETENTION_DAYS` are pruned in batches; poll
  definitions and instance rows are retained until the poll is deleted.
- `/deletepoll` deletes responses immediately and redacts messages; the retention job is the fallback
  for closed instances that were never deleted.
- No names, usernames, last names, participant IDs, or message bodies in logs/metrics.

## 4. State and concurrency

### 4.1 State machines

**Poll:** `draft` → `active` (first publish) → `deleted` (soft). A deleted poll cannot be published;
its instances are closed/deleted and its responses removed.

**Instance:** `open` → `closed` → `open` (reopen); `open`/`closed` → `deleted` (poll delete). A closed
instance rejects votes; a deleted instance rejects votes and renders redacted.

**Message:** `pending` → `sending` → `sent` | `failed` | `skipped`; `sent` → `missing` (Telegram says
the message is gone); `failed` → `pending` (retry via `/refresh`); `pending` → `skipped` (chat
inactive, instance closed/deleted). A `sending` row at startup becomes `failed` with
`ambiguous_outcome_after_restart`.

**Response:** present/absent per `(instance, option, user)`. No history is kept.

### 4.2 Atomic operations and uniqueness

- Publish: one transaction inserts the instance and all message rows. The partial unique indexes
  prevent duplicate `(instance, chat)` and `(instance, inline_message_id)` rows.
- Vote: a transaction reads the instance (open), the option (belongs to the poll), and the existing
  response; inserts or deletes. The unique index collapses concurrent toggles. The last writer wins
  for a double-tap; this is acceptable for a toggle.
- Worker claim: `UPDATE poll_messages SET status='sending', attempt_count=attempt_count+1,
  started_at=? WHERE id=? AND status='pending'`; only the row that changed is sent. SQLite's
  single-writer model plus the status predicate prevents double sends.
- Broadcaster: one task per instance; a dirty flag is set by votes; the task debounces, renders once,
  and edits messages sequentially with a semaphore. If a vote arrives during the edit, the dirty flag
  is set again and the loop repeats.

### 4.3 Crash and restart

- Pending messages remain claimable.
- `sending` messages become `failed` with `ambiguous_outcome_after_restart`; they are not retried
  automatically. `/refresh` can retry them after an admin inspects.
- Sent messages may be stale after a restart because the broadcaster queue is in memory. The next vote
  or `/refresh` re-renders them. This is acceptable and documented.
- The single-instance lock prevents a second process from sending.

### 4.4 Rate limiting and backpressure

- Vote bucket: per user, `VOTES_PER_MINUTE`, in-memory, reset on restart.
- Edit concurrency: `MESSAGE_EDIT_CONCURRENCY` semaphore; `429 retry_after` clamped to 300 s; bounded
  retries `MESSAGE_EDIT_RETRY_ATTEMPTS`.
- Send limits: reuse the existing per-chat/global token buckets and `SEND_CONCURRENCY`.
- The broadcaster queue is bounded; if it is full, the dirty flag is set and the task processes it
  later; votes are never dropped from the database.

## 5. Alternatives considered

| Alternative | Pros | Cons | Why rejected |
|---|---|---|---|
| Keep native polls and add a separate RSVP message | No vote storage, minimal change | Does not behave like CountMeIn; names are not attached to options; two products | G1 Q-1 chose replacement. |
| Add CountMeIn alongside native polls | Backward compatible | Two schemas, two UX paths, double the test matrix, dead native path | G1 Q-1 chose replacement; no production data. |
| Per-chat response lists | No cross-chat leakage | Does not match CountMeIn parity; a poll posted to several chats shows different lists | G1 Q-8 chose global per occurrence. |
| Immediate edit on every vote | Simple | Telegram rate limits and message churn; large groups throttle | Coalescing with a 1 s debounce is safer and still p95 ≤2 s. |
| Inline-only publishing (CountMeIn parity) | No chat registration | No scheduled auto-post to registered chats; BotFather setup | G1 Q-7 chose registered + inline; inline is SPEC-005. |
| Hard-delete polls and messages | Simple | Loses audit/history; Telegram copies remain | Soft-delete + redaction + response deletion. |
| Keep the old scheduler running | No regression window | It sends native polls, which no longer exist | Replace with a heartbeat; SPEC-004 restores scheduling on the new model. |
| Store usernames/last names | Richer display | More personal data; G1 Q-5 chose first name only | Store only user ID + first name. |

## 6. Test strategy

- **Unit boundaries:** validation, rendering, state machines, callback codec, auth decisions, retry
  classification, retention selection. Pure functions, fake clock, no I/O.
- **Integration:** repositories on a temporary SQLite file with migrations; publish → worker → sent;
  vote → broadcaster → edits; close/reopen/delete; retention; health/metrics via aiohttp test client.
- **Fakes:** `FakeClock`, `FakeMessageGateway` (records sends/edits, fails by error class),
  `FakeNotifier`, `FakeChatGateway`. No network.
- **Required matrix:** title/option/name boundaries; multi-select; duplicate names; 4,096 rendering;
  five rapid toggles → one edit; `not modified`; `not found`; 429; 5xx; inactive chat; closed instance;
  duplicate worker tick; crash recovery; destructive migration guard; retention idempotency; callback
  ≤64 bytes; non-admin vote; forged callback; admin callback in a group rejected.
- **Coverage plan:** 100% line and branch. Deleted native-poll modules and their tests are removed in
  the same change; no new `# pragma: no cover`.

## 7. Observability plan

- **Logs:** stable event names from spec §11; structured `extra` fields; no names or participant IDs.
  Correlation: `poll_id`, `instance_id`, `message_id`, `option_id`, `delivery_id`; admin `update_id`
  for commands. Vote logs carry `option_id` and aggregate counts only.
- **Metrics:** spec §11. Histogram `tap_vote_to_edit_seconds`; counters for votes, rejections, edits,
  coalescing, retention. Existing build/DB/bot metrics remain. No chat/user labels.
- **Health:** `/livez` process-only; `/healthz` DB + posting-worker heartbeat; `/metrics` as before.
- **Alerts:** terminal post failure (DM admins once per message), missing messages found by `/refresh`,
  repeated edit failures (warning), disk/backup/database alerts unchanged.
- **SLOs:** vote-to-edit p95 ≤2 s, zero duplicate posts per instance/chat, admin read p95 ≤2 s.

## 8. Security plan

- **Authorization points:**
  - Commands: private chat + `ADMIN_USER_IDS` (existing `AdminGuard`).
  - Admin callbacks: private chat + allowlist + admin namespace.
  - Vote callbacks: no allowlist; validate the stored message/instance/option; actor is
    `from_user.id`; rate limit per user.
  - Unknown namespaces and malformed data are rejected before any mutation.
- **Validation points:** title/option/name lengths, option uniqueness, callback parsing, chat
  registration, instance state, option ownership, message ownership.
- **Secret handling:** unchanged; token is a `SecretStr`, redacted centrally; no new secret.
- **Abuse controls:** vote bucket, edit concurrency, send limits, destructive confirmations, admin
  command bucket. A forged callback can only change the sender's own response.
- **Privacy:** only user ID + first name; deletion and retention; no names in logs/metrics; cross-chat
  visibility disclosed in the publish confirmation and the runbook.

## 9. Rollout and rollback

- **Order:** merge the planning PR (constitution amendment + ADRs + spec/plan/tasks) first; then the
  implementation PR; then tag `v0.3.0-alpha`.
- **Feature flags:** none. The destructive migration flag is one-time and removed after the run.
- **Migration backup:** mandatory pre-migration backup; abort on failure. The migration guard aborts
  if old rows exist and the flag is unset.
- **Rollback:** restore the pre-migration backup and redeploy v0.1.0. The new schema is not readable by
  the old image. Record the data-loss window (writes after the backup).
- **Monitoring after deploy:** `/healthz`, `/status`, a test poll in a staging chat, vote from two
  accounts, close/reopen/delete, and a log check for `poll_message_failed`/`message_edit_failed`.

## 10. Task sequencing

1. **Planning/amendment:** apply the constitution v2.0.0 amendment, add ADR-0003/0004, close/supersede
   SPEC-002 after G2.
2. **Domain + tests:** entities, enums, validation, renderer, callback codec.
3. **Persistence + migration:** models, repositories, migration 0002, guard.
4. **Services:** poll/publish/response/retention/recovery/audit.
5. **Worker + broadcaster:** send, edit, coalesce, retry, shutdown.
6. **Bot UX:** auth split, keyboards, render, wizard, commands, gateway.
7. **Runtime + config:** wiring, heartbeat, metrics, health.
8. **Docs + ops:** README, runbook, `.env.example`, CHANGELOG.
9. **Verification:** full suite, Docker, G3, governance, G4, PR.

The authoritative task list is [`tasks.md`](tasks.md).

## 11. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cross-chat name leakage surprises users | Medium | High | Publish confirmation states it; document the trust boundary; G1 Q-8 recorded; per-chat isolation is a future spec. |
| Telegram edit rate limits in large groups | Medium | Medium | Debounce + coalescing, bounded concurrency, 429 backoff, `/refresh`. |
| Respondent data is personal data | High | High | Minimal fields, no logs, deletion, retention, runbook deletion procedure. |
| Destructive migration loses data | Low | High | Guard aborts when old rows exist; mandatory backup; flag is explicit. |
| Temporary loss of scheduling (v0.3.0-alpha) | High | Medium | Release is alpha; do not deploy to production; SPEC-004 restores scheduling; README states it. |
| Message exceeds 4,096 characters | Medium | Medium | Renderer reduces names deterministically; tests at the boundary; labels/title limits prevent overflow. |
| Duplicate names confuse participants | Medium | Low | Duplicates allowed by design; names are snapshots; counts are per option. |
| Callback data over 64 bytes | Low | Medium | Codec tests every generated key; identifiers only. |
| Stale messages after restart | Medium | Low | Next vote or `/refresh` re-renders; `/status` exposes failures. |
| Deleted Telegram messages | Medium | Low | Mark `missing`, skip retries, alert on refresh; no crash. |
| Coverage drops after deleting native code | Medium | Medium | Remove obsolete tests with their code; add new tests first; 100% gate. |
| Branch staleness / SPEC-002 conflict | Medium | Medium | Start from `main`; supersede #4; do not merge the old branch. |

## 12. Open questions

None at G2. All G1 questions are answered in [`spec.md`](spec.md) §14. Any new ambiguity discovered
during implementation is recorded in the spec change log and, if material, returns to G2.
