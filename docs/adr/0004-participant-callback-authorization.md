# ADR-0004: Participant callback authorization for sign-up polls

| Field | Value |
|---|---|
| Status | Accepted — G2 approved 2026-11-02 |
| Date | 2026-11-02 |
| Decision owner | @ReivaxBoido |
| Supersedes | None |
| Related | SPEC-003, issue #7, ADR-0002, ADR-0003 |

## Context

The existing bot accepts callbacks only from allowlisted admins (`AdminGuard.authorize_callback`). Every
callback namespace (`p`, `s`, `w`) is an admin action. CountMeIn-style polls invert this: the people who
press the option buttons are ordinary members of a group, supergroup, or channel, and they must not be
in `ADMIN_USER_IDS`.

The callback channel is also the only write path available to a participant. Telegram callback data is
opaque to users but is not a security boundary: a determined user can craft a callback with known IDs.
The bot must ensure that a forged callback can change only the sender's own response, never another
user's response or an admin action.

Constraints:

- Callback data is limited to 64 bytes.
- Callbacks from regular messages carry `message.chat.id` and `message.message_id`; inline-message
  callbacks carry `inline_message_id` and no chat.
- Channels do not expose reliable membership for every reader; `getChatMember` is not a universal
  gate.
- The vote path must answer within 3 seconds and must not block on extra Telegram calls.
- Admin commands remain DM-only and allowlist-only (ADR-0002).

## Decision

We will **split callback authorization into admin callbacks and participant vote callbacks**, with a
stored-message check for votes.

Key details:

1. Callback namespaces are `v1:v` (vote), `v1:p` (poll admin action), `v1:t` (target toggle), `v1:w`
   (wizard), and `v1:c` (confirmation).
2. Admin namespaces require a **private chat** and `from_user.id` in `ADMIN_USER_IDS`. Admin callbacks
   from groups/channels are rejected.
3. The vote namespace does **not** use the allowlist. It is validated against persistence:
   - `from_user.id` is present;
   - the data parses as `v1:v:<instance_id>:<option_id>`;
   - a `poll_messages` row exists for the instance and matches the callback's chat/message (or
     `inline_message_id` in SPEC-005);
   - the instance is `open` and the poll is not `deleted`;
   - the option belongs to the instance's poll.
4. The actor is always `callback.from_user.id`; callback data is never treated as identity. A forged
   callback can only add or remove the sender's own response.
5. Votes are rate-limited per user (`VOTES_PER_MINUTE`, default 30) with an in-memory token bucket.
6. The bot does **not** call `getChatMember` on every vote. Telegram only delivers callbacks to users
   who can see the message, and the stored-message/instance/option checks bound the damage of a forged
   callback. This avoids latency and channel-membership limitations.
7. Every callback receives an answer within 3 seconds, including refusals (`Voting is closed`,
   `That option no longer exists`, `Too many votes`).
8. Unknown namespaces, malformed data, and non-integer IDs are rejected before any mutation.

## Alternatives considered

| Alternative | Pros | Cons | Why not chosen |
|---|---|---|---|
| Add voters to `ADMIN_USER_IDS` | Reuses the existing guard | Impossible for public groups; violates the product | Not CountMeIn parity. |
| Verify every voter with `getChatMember` | Stronger membership check | Extra Telegram call per vote; fails for channel readers; latency | Not reliable across target chat types. |
| Signed callback tokens | Tamper-evident | 64-byte limit; key management; overkill for "toggle my own name" | The stored-message check is sufficient. |
| Let the message text carry the user list | No database auth | Cannot toggle reliably; races; no redaction | Not viable. |
| Inline-only voting | Telegram handles message access | No scheduled registered-chat posts; BotFather setup | SPEC-005, not the core. |
| Keep all callbacks admin-only | Simple security model | Participants cannot vote | Defeats the product. |

## Consequences

### Positive

- Ordinary chat members can vote without being admins.
- Admin actions remain DM-only and allowlist-only; the two paths are explicit and separately tested.
- A forged callback can only affect the sender's own response.
- The vote path stays fast: one database read/validation and no extra Telegram call.

### Negative

- The bot relies on Telegram's delivery of callbacks as the membership signal; a user who leaves a chat
  may still be able to press an old button if Telegram delivers it. The impact is limited to their own
  name and is rate-limited.
- The stored-message check requires a `poll_messages` row per posted message; deleting the row or the
  message invalidates votes.
- The callback namespace set grows; the codec and tests must cover every generated key.

### Neutral / follow-up work

- If abuse appears, add a per-chat vote budget or a membership check behind a config flag.
- SPEC-005 extends the same validation to `inline_message_id`.
- The runbook documents how to deactivate a chat and delete a poll to stop votes.

## Compliance and verification

- `CONSTITUTION.md` v2.0.0 §6.1 and §10.2 describe the split.
- `bot/auth.py` exposes separate admin and vote authorization functions; no handler trusts callback
  data for identity.
- Unit tests cover: non-admin vote accepted; admin callback in a group rejected; forged callback for
  another chat rejected; closed/deleted/unknown option rejected; rate limit; ≤64-byte keys.
- Integration tests cover a vote in chat A editing messages in chats A and B.
- Logs record refusals without names or participant IDs; metrics count rejections by reason.

## Links

- Spec: [`specs/003-countmein-core/spec.md`](../../specs/003-countmein-core/spec.md)
- Issue: #7
- Related ADRs: ADR-0002 (Telegram-only admin), ADR-0003 (product pivot)
