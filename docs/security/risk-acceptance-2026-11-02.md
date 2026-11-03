# Security risk acceptance — base image HIGH findings

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-11-02 |
| Owner | @ReivaxBoido |
| Expiry | 2026-12-31 |
| Scope | `ghcr.io/reivaxboido/telegram-auto-poll:latest` (`python:3.12-slim`, Debian 13.7) |
| Findings | 45 HIGH, 0 CRITICAL, 0 fixable at scan time |
| Follow-up | issue #8 |

## Context

Trivy 0.76.1 scanned the SPEC-003 image during the governance gate. All findings are in Debian base
OS packages with no fixed version available at scan time:

- `util-linux` / `bsdutils` / `mount` / `login` / `libblkid1` / `libmount1` / `libsmartcols1` /
  `libuuid1` / `liblastlog2-2` (CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410)
- `ncurses` / `libncursesw6` / `libtinfo6` (CVE-2025-69720)
- `perl-base` (CVE-2026-9538)
- `libsqlite3-0` (CVE-2026-119373)
- `libsystemd0` / `libudev1` (CVE-2026-16742)
- `libacl1` (CVE-2026-54369)

No application-layer vulnerabilities were found. `pip-audit` reported no known vulnerabilities,
`bandit` reported no issues, and `gitleaks` found no leaks in the working tree or full history.

## Decision

The maintainer accepts the 45 HIGH findings until **2026-12-31** because:

- no fixed Debian packages were available at scan time;
- the runtime image is non-root (UID/GID 10001), read-only root filesystem, `cap_drop: ALL`,
  `no-new-privileges`, no Docker socket, and no host network;
- the bot only makes outbound HTTPS to Telegram and serves health/metrics on host loopback;
- the vulnerable utilities are not exposed as network services.

This is a time-boxed exception under `CONSTITUTION.md` §10.3 and §18.5. It is not a waiver of the
quality gates: a fixed base image must be adopted as soon as one is available.

## Conditions

1. Re-scan the image before 2026-12-31 and after every base-image update.
2. If Debian publishes fixes, rebuild with the updated `python:3.12-slim` digest and re-run the full
   governance gate.
3. Do not extend the expiry without a new maintainer decision and a new record.
4. If a CRITICAL finding appears, stop and reassess immediately; this acceptance does not cover it.

## Evidence

```bash
trivy image --scanners vuln --severity HIGH,CRITICAL \
  ghcr.io/reivaxboido/telegram-auto-poll:latest
```

Result: 45 HIGH, 0 CRITICAL, 0 fixable.

Follow-up: issue #8.
