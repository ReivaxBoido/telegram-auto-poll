# Operations runbook

This runbook is the authoritative procedure for deploying and operating `telegram-auto-poll`. It is
updated in the same PR as any operational change. See `CONSTITUTION.md` §14–§16 for the rules behind
these procedures.

## Assumptions

- Deployment directory: `/opt/telegram-auto-poll`
- Compose service: `bot`
- Data volume: `poll-data`, mounted at `/data`
- Database: `/data/telegram-auto-poll.db`
- Health endpoint: `http://127.0.0.1:8080/healthz`
- Bot image: `ghcr.io/<owner>/telegram-auto-poll`
- The host runs NTP and Docker with a non-root deploy user.
- The `sqlite3` CLI is installed on the host for online backups and integrity checks.

Replace `<owner>`, `<version>`, and `<chat_id>` with real values. Never paste the bot token into a shell
history, ticket, or chat.

## 1. First deployment

1. Create the bot with [@BotFather](https://t.me/botfather) and copy the token.
2. Create `.env` from `.env.example`:
   ```bash
   install -m 600 /dev/null .env
   $EDITOR .env
   ```
   Set `TELEGRAM_BOT_TOKEN`, `ADMIN_USER_IDS`, and `DEFAULT_TIMEZONE`.
3. Prepare the data volume and permissions:
   ```bash
   docker volume create poll-data
   docker compose up -d bot
   ```
4. Check health and logs:
   ```bash
   docker compose ps
   curl -fsS http://127.0.0.1:8080/healthz
   docker compose logs --tail=100 bot
   ```
5. In Telegram, each admin opens a **DM** with the bot and presses **Start**.
6. Verify `/status` and `/version` from an allowlisted admin.
7. Register a staging chat with `/addchat`, create a test poll with `/newpoll`, and `/publish` it.
   Confirm the poll arrives once and that a second account can add its name.
8. Enable the host backup cron (§7) and the external health probe before adding production chats.

**Rollback:** stop the container, fix `.env`, and start again. No data is written before a successful
startup.

## 2. Routine upgrade

1. Confirm the release tag and changelog on GitHub; confirm CI and image scans are green.
2. Take an ad-hoc backup (§7) if the release includes a migration.
3. Pull and restart:
   ```bash
   cd /opt/telegram-auto-poll
   docker compose pull bot
   docker compose up -d bot
   ```
4. Verify:
   ```bash
   docker compose ps
   curl -fsS http://127.0.0.1:8080/healthz
   docker compose logs --since=5m bot | grep -E 'startup_complete|migration|ERROR'
   ```
5. DM `/status`; confirm the version matches the tag and the counts are correct.
6. Send a test sign-up poll to a staging chat if the change touches delivery.

Do not use `latest` in production. Pin the immutable `vX.Y.Z` tag in Compose.

## 3. Rollback

1. Set the Compose image back to the previous `vX.Y.Z` tag.
2. `docker compose up -d bot`
3. Verify `/healthz`, `/version`, and `/status`.
4. If the failed release applied an incompatible migration:
   1. Stop the container.
   2. Restore the pre-migration backup (§8).
   3. Start the previous image.
   4. Record the data-loss window in the incident log.

Image-only rollback is expected to work for one release because migrations are backward-compatible.
Do not attempt a downgrade migration.

## 4. Health verification

| Check | Command | Expected |
|---|---|---|
| Container | `docker compose ps` | `healthy` |
| Liveness | `curl -fsS http://127.0.0.1:8080/livez` | 200 `{"status":"ok"}` |
| Readiness | `curl -fsS http://127.0.0.1:8080/healthz` | 200, DB and posting worker ok |
| Metrics | `curl -fsS http://127.0.0.1:8080/metrics` | Prometheus text |
| Bot | DM `/status` | version, counts, recent failures |
| Logs | `docker compose logs --tail=200 bot` | no traceback, no token |

A 503 from `/healthz` includes a reason list. Do not restart blindly; read the reason first.

## 5. Logs

```bash
docker compose logs -f --tail=100 bot
docker compose logs --since=1h bot | grep '"level":"ERROR"'
docker compose logs --since=1h bot | grep -E "message_id|instance_id"
```

Logs are JSON on stdout and rotated by the Docker `json-file` driver (10 MB × 3). For longer retention,
ship them to the host’s log store. Never enable `DEBUG` in production for more than a few minutes; it
increases volume and may include sensitive operational context.

## 6. Metrics and alerts

Scrape `127.0.0.1:8080/metrics` from the host or a local Prometheus. Minimum alerts:

- `/healthz` non-200 for 5 minutes.
- `tap_worker_heartbeat_age_seconds > 45` for 2 minutes.
- Any increase in `tap_send_failures_total{reason="exhausted"}`.
- Disk usage > 80%.
- No successful backup in 26 hours.

Every alert must name the runbook section to follow. See `CONSTITUTION.md` §16.4.

## 7. Backup

Daily host cron at 03:00 UTC:

```bash
#!/bin/sh
set -eu
DB=/var/lib/docker/volumes/poll-data/_data/telegram-auto-poll.db
DEST=/var/backups/telegram-auto-poll
mkdir -p "$DEST"
STAMP=$(date -u +%Y-%m-%d)
sqlite3 "$DB" ".backup '$DEST/telegram-auto-poll-$STAMP.db'"
sqlite3 "$DEST/telegram-auto-poll-$STAMP.db" "PRAGMA integrity_check;" | grep -qx ok
chmod 600 "$DEST/telegram-auto-poll-$STAMP.db"
find "$DEST" -name 'telegram-auto-poll-*.db' -mtime +14 -delete
```

- Keep 14 daily and 4 weekly copies; copy one encrypted backup off-host.
- Do not use `cp` on a live database. Use `.backup`.
- Verify the job by checking the file timestamp and the integrity output.
- A backup is not valid until a restore drill succeeds (§17).

## 8. Restore

1. Announce the maintenance window and stop the bot:
   ```bash
   docker compose stop bot
   ```
2. Preserve the current files:
   ```bash
   cp -a /var/lib/docker/volumes/poll-data/_data /var/backups/poll-data-broken-$(date -u +%s)
   ```
3. Copy the chosen backup into the volume and fix ownership:
   ```bash
   cp /var/backups/telegram-auto-poll/telegram-auto-poll-YYYY-MM-DD.db \
      /var/lib/docker/volumes/poll-data/_data/telegram-auto-poll.db
   chown 10001:10001 /var/lib/docker/volumes/poll-data/_data/telegram-auto-poll.db
   chmod 600 /var/lib/docker/volumes/poll-data/_data/telegram-auto-poll.db
   ```
4. Remove stale `-wal` and `-shm` files **only while stopped**:
   ```bash
   rm -f /var/lib/docker/volumes/poll-data/_data/telegram-auto-poll.db-wal \
         /var/lib/docker/volumes/poll-data/_data/telegram-auto-poll.db-shm
   ```
5. Start and verify:
   ```bash
   docker compose up -d bot
   curl -fsS http://127.0.0.1:8080/healthz
   ```
6. DM `/status`; confirm counts and recent failures. Send a test sign-up poll to staging.
7. Record the restore, backup date, and data-loss window in the incident log.

## 9. Rotate the bot token

1. In BotFather, revoke the current token and generate a new one.
2. Update `.env` (or the Docker secret) with the new token; do not paste it into chat.
3. Restart:
   ```bash
   docker compose up -d --force-recreate bot
   ```
4. Verify `/healthz`, `/status`, and a test DM.
5. Confirm the old token is rejected by attempting `getMe` with it from a secure shell (do not save the
   command with the token).
6. If the bot is suspected compromised, also review `audit_events`, remove unexpected chats, and follow
   the incident process.

## 10. Admin lockout

Symptoms: an admin gets “This bot is private.” or no response.

1. Confirm the Telegram user ID is in `ADMIN_USER_IDS` (comma-separated, no spaces required but
   tolerated).
2. Confirm the admin is messaging the bot in a **private** chat and has pressed **Start**.
3. Update `.env`, then `docker compose up -d --force-recreate bot`.
4. Verify with `/status` from the admin.
5. If no admin can access the bot, use the host operator account to inspect logs and the database; do
   not add a public backdoor.

## 11. Bot removed from a chat

Symptoms: a post fails with a non-retryable permission/`chat not found` error; admins receive an alert.

1. Re-add the bot to the group/channel.
2. For channels, grant administrator with “Post messages”.
3. In DM, `/chats`, select the chat, and reactivate it if it was deactivated.
4. `/refresh <poll_id>` to retry failed messages and re-render the latest instance.
5. Confirm the message appears and votes are accepted.

## 12. Posting worker stalled

Symptoms: `/healthz` 503 with a worker heartbeat reason; pending posts are not sent.

1. Check logs for a blocked event loop, a long DB transaction, or repeated Telegram timeouts.
2. Check disk and DB health:
   ```bash
   df -h
   docker compose exec bot python -c "import sqlite3; print(sqlite3.connect('/data/telegram-auto-poll.db').execute('PRAGMA integrity_check').fetchone())"
   ```
3. If the process is wedged, `docker compose restart bot`.
4. Verify the heartbeat metric and `/healthz`.
5. Inspect pending messages and failures via `/status`; use `/refresh` to retry a specific poll.

## 13. Migration failure

Symptoms: container exits non-zero during startup; logs show an Alembic error; no bot.

1. Do not delete the database.
2. Inspect the pre-migration backup in `/data/backups/`.
3. Read the migration error and the current `alembic_version`.
4. For migration `0002`, the old native-poll tables are dropped. The migration aborts if they
   contain rows and `ALLOW_DESTRUCTIVE_MIGRATION` is false. Take a verified backup, set the flag to
   `true` for that one run, and remove it afterwards.
5. If the release is faulty, redeploy the previous image and restore the pre-migration backup; the
   new schema is not readable by v0.1.0.
6. Open an incident and add a regression test for the migration.

## 14. Database corruption

Symptoms: `SQLITE_CORRUPT`, failed `integrity_check`, or repeated I/O errors.

1. Stop the container immediately to prevent further writes.
2. Preserve the database, WAL, and SHM files.
3. Run `PRAGMA integrity_check` on a copy, not the live file.
4. Restore the most recent verified backup (§8).
5. If the backup is also corrupt, escalate; do not attempt ad-hoc row deletion.
6. Record the incident and add a backup/restore drill action.

## 15. Disk full

1. Identify the consumer: Docker logs, backups, or the database.
2. Rotate/remove old logs and backups; never delete the live database or WAL.
3. Free at least 20% of the volume.
4. Restart if the process is unhealthy.
5. Add or adjust monitoring; investigate unexpected log volume.

## 16. Clock drift

1. Check `timedatectl` / `chronyc tracking` on the host.
2. Fix NTP; restart the container only if the health check is failing.
3. Review `tap_vote_to_edit_seconds` and `tap_message_edits_total` around the drift window.
4. Do not manually edit message or instance rows in the database; use `/refresh` or `/publish`.

## 17. Restore drill (quarterly)

- [ ] Pick the most recent off-host backup.
- [ ] Restore into a staging volume or a temporary host.
- [ ] Start the container with a staging bot token.
- [ ] Verify `/healthz`, `/status`, poll/instance counts, and a test sign-up poll.
- [ ] Record the date, backup date, duration, and result below.

| Date | Backup date | Duration | Result | Operator |
|---|---|---|---|---|
| | | | | |

## 18. Incident checklist

1. Declare severity and an incident owner.
2. Contain: stop the bot or revoke the token if compromise is suspected.
3. Preserve logs, database, and backups before changing state.
4. Communicate impact to chat owners if polls were mis-sent or data was exposed.
5. Recover using the relevant section above.
6. Verify with `/status`, a test poll, and metrics.
7. Write a blameless postmortem in `docs/incidents/` within five working days.
8. Add a regression test or runbook change, and link it from the postmortem.


## 19. Response deletion and retention

Responses are personal data: a Telegram user ID, a first-name snapshot, and timestamps.

1. A participant can remove their own name by tapping the option again.
2. An admin can run `/deletepoll <id>` to soft-delete the poll, delete every response row, and edit
   every posted message to “This poll was deleted.” with no keyboard.
3. Closed/deleted instances are pruned after `RESPONSE_RETENTION_DAYS` (default 90). The retention
   job attempts to redact the message first, then deletes the responses. Telegram copies already seen
   by users cannot be recalled; state this limitation when handling a deletion request.
4. Never copy responses into tickets, logs, or chat. Use `/status` for counts only.

## 20. Upgrade from the native-poll release (v0.1.0 → v0.3.0-alpha)

1. Take an ad-hoc backup (§7) and verify `PRAGMA integrity_check`.
2. Confirm the old database has no polls, targets, schedules, or deliveries that must be preserved.
   The migration aborts if any old table has rows and the flag is false.
3. Set `ALLOW_DESTRUCTIVE_MIGRATION=true` in `.env` for one run.
4. `docker compose pull bot && docker compose up -d bot`.
5. Verify `/healthz`, `/status`, `/addchat`, `/newpoll`, `/publish`, a vote from a second account, and
   `/deletepoll`.
6. Remove `ALLOW_DESTRUCTIVE_MIGRATION` from `.env` and recreate the container.
7. Scheduling is not available in v0.3.0-alpha; do not deploy it as a production replacement until
   SPEC-004 (v0.4.0) restores scheduled posting.
