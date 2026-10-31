# Security policy

## Supported versions

Only the latest released `vX.Y.Z` image is supported with security fixes. Older tags may be asked to
upgrade before a report is investigated.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub’s private vulnerability reporting on this repository (Security → Report a vulnerability), or
contact the maintainers through a private channel agreed in advance. Include:

- affected version or commit,
- a description of the issue and its impact,
- reproduction steps or a proof of concept,
- any relevant sanitized logs,
- whether the bot token, admin access, or chat data may be exposed.

Never include a live bot token. If a token is exposed, revoke it in [@BotFather](https://t.me/botfather)
immediately, then report.

## Response

Maintainers aim to acknowledge a report within three business days and to provide a remediation plan
within ten business days. Timelines are best-effort for a private, single-operator project.

## Scope

In scope:

- unauthorized admin access or privilege escalation,
- bot token or secret exposure,
- injection into bot messages, logs, or the database,
- duplicate or unauthorized poll delivery,
- container or dependency vulnerabilities that affect this deployment.

Out of scope:

- Telegram platform vulnerabilities (report them to Telegram),
- spam or abuse by a chat owner who controls the bot,
- issues requiring physical access to the host,
- social engineering.

## Disclosure

Please allow maintainers time to fix the issue before public disclosure. Credit is given in the release
notes unless the reporter prefers to remain anonymous.
