# SPEC-002: Buttons-first poll wizard

| Field | Value |
|---|---|
| Spec ID | SPEC-002 |
| Status | Clarified — G1 closed; awaiting G2 |
| Owner | @ReivaxBoido |
| Issue | #4 |
| Plan | [`plan.md`](plan.md) — awaiting G2 |
| Tasks | [`tasks.md`](tasks.md) — awaiting G2 |
| Target release | v0.2.0 |
| Delivered in | TBD |

> **Gate status:** G1 answered 2026-10-31 (Q-1…Q-7). No code changes yet.

## 1. Problem

The `/newpoll` wizard is controlled by typed words (`next`, `done`, `back`, `toggle`, `skip`). The
admin cannot see which actions exist, mistyped words look like invalid text, and two steps are hard to
read: target chats appear as bare ids (`#1`), and the preview joins options with commas. SPEC-001
shipped this and the live check confirmed the flow works, but the UX is below the bar.

## 2. Goals

- G-1: an admin can complete the whole wizard without typing a control word.
- G-2: target chats are recognisable by name, with visible selection state and pagination.
- G-3: the preview lists options and target names line by line.

## 3. Non-goals

- NG-1: editing, duplicating, archiving, or resending polls (still deferred).
- NG-2: schema changes; this is a bot-layer change.
- NG-3: inline-button selection of timezones, dates, or the one-off instant (typed inputs stay).
- NG-4: changing the delivery, scheduler, or persistence behaviour from SPEC-001.

## 4. Users and stories

| Persona | Story |
|---|---|
| Admin | I want visible controls so I do not have to remember words. |
| Admin | I want to pick a chat by its name so I do not confuse groups. |
| Admin | I want a preview I can scan before confirming. |

## 5. Functional requirements

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-201 | Every wizard step MUST render an inline keyboard with Back and Cancel; steps with a forward action also show Next/Done/Skip as applicable. | P0 | Given any step, then Back and Cancel are present. |
| FR-202 | Control buttons MUST be sufficient to complete the wizard without typed control words. | P0 | Given button-only input, then a poll is created. |
| FR-203 | Question, description, option text, timezone, start/end dates, and the one-off instant MUST remain typed inputs. | P0 | Buttons never attempt these values. |
| FR-204 | Target selection MUST label each button with the chat title (truncated to 40 characters), the id, and a selected marker. | P0 | Given a title, then the label contains it and the id. |
| FR-205 | Target selection MUST paginate at 6 chats per page with Previous/Next (7 chat rows + navigation + actions would exceed
the 8-row keyboard limit), and require at least one target. | P0 | Given 9 chats, then page 2 shows 2. |
| FR-206 | Target buttons MUST toggle selection in place, and Done MUST advance only with ≥1 selected. | P0 | Two taps select then clear; empty Done re-prompts. |
| FR-207 | Anonymity and multiple-answer MUST be toggled by buttons that show their current state. | P1 | Label shows Anonymous/On or Single/Off. |
| FR-208 | Schedule kind MUST be chosen with buttons: daily, weekly, monthly, one-off, save without schedule. | P0 | Each choice advances to the right step. |
| FR-209 | Weekly weekdays MUST use seven toggle buttons (Mon–Sun) plus Done, enforcing 1–7 distinct days. | P1 | All seven can be toggled; empty Done re-prompts. |
| FR-210 | Options MUST offer Add another (then typed text), Remove for each option, and Done enforcing 2–12 unique options. | P1 | Remove deletes only that option; Done with one re-prompts. |
| FR-211 | The preview MUST show a numbered option list, the target chat names, the schedule summary, and next five runs. | P0 | Each option is on its own numbered line. |
| FR-212 | Text shortcuts (`next`, `done`, `back`, `toggle`, `skip`, `confirm`, chat ids) MUST keep working as a fallback. | P2 | Typed `back` still moves back. |
| FR-213 | Every callback MUST be ≤ 64 bytes, versioned `v1`, and round-trip through the codec. | P0 | Codec tests cover every generated key. |
| FR-214 | Keyboards MUST stay within 8 rows and 4 buttons per row; messages ≤ 4,096 characters. | P0 | Layout tests at the boundaries. |
| FR-215 | Poll content MUST remain plain text; no parse mode. | P0 | `<b>` survives literally. |

## 6. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-201 | Callback queries are answered within 3 s; heavy work is acknowledged first. |
| NFR-202 | No schema, migration, delivery, or scheduler change; SPEC-001 invariants still hold. |
| NFR-203 | 100% line and branch coverage; no new `# pragma: no cover`. |
| NFR-204 | No network in unit/integration tests. |

## 7. Edge cases

| Case | Behaviour |
|---|---|
| Chat title > 40 chars | Truncate with `…`; id still shown. |
| 7+ chats | Paginate 6 per page; selection persists across pages. |
| Zero active chats | Targets step explains `/addchat` and keeps Cancel/Back. |
| Removal of a selected target | Selection cleared; Done re-checks ≥1. |
| 12 options | Add-another button hidden; Done remains. |
| One option | Done re-prompts with the constraint. |
| Stale callback after TTL | Answer “wizard expired”, discard session. |
| Callback for another admin | Refused by the authorizer; no state change. |

## 8. Test plan

- Codec: every new key, round-trip, ≤ 64 bytes.
- Keyboards: row/button limits, pagination boundaries, truncation, selected markers.
- Engine: weekday toggles, chat pagination/toggle, option removal, button-only transitions.
- Handlers: callback routing for every action, text fallback, stale/foreign callbacks.
- Render: numbered preview with long options and chat names.
- End-to-end handler test: `/newpoll` → buttons only → poll + schedule created.

## 9. Clarifications (G1)

| # | Question | Answer |
|---|---|---|
| Q-1 | New SPEC-002 or amend SPEC-001? | New SPEC-002 depending on SPEC-001. |
| Q-2 | Keep typing for free-text fields? | Yes. |
| Q-3 | Chat label and pagination? | `Title (id)`, selected marker, ~40 chars, 7/page + Prev/Next. |
| Q-4 | Multi-select toggles + Done? | Yes. |
| Q-5 | Weekday picker? | Seven toggles + Done. |
| Q-6 | Keep text shortcuts? | Keep as a fallback. |
| Q-7 | Preview layout? | Numbered options, names, schedule summary, next five. |

**G1:** answered by @ReivaxBoido on 2026-10-31 (“all recommended. 6. keep them”).

## 10. Gate record

| Gate | Artifact | Approved by | Date |
|---|---|---|---|
| G1 | Clarifications §9 | @ReivaxBoido | 2026-10-31 |
| G2 | spec, plan, tasks | | |
