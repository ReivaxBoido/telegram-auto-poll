# Plan: SPEC-002 — Buttons-first poll wizard

| Field | Value |
|---|---|
| Spec | [`spec.md`](spec.md) |
| Status | Proposed — awaiting G2 |
| Author | @ReivaxBoido |
| ADRs | none (SPEC-001 ADRs unchanged) |

## 1. Approach

Keep the pure `WizardEngine` and its state machine; add button-oriented events and an inline keyboard
renderer per step. Handlers translate callbacks into `WizardInput`, and text input keeps working as a
fallback. No layer boundary moves: `bot/wizards` stays pure, `bot/keyboards` builds markup, the handler
owns I/O and services.

Callback scheme (all ≤ 64 bytes):

| Action | Data |
|---|---|
| Back / Cancel | `v1:w:back`, `v1:w:cancel:chat` |
| Next / Done / Skip | `v1:w:next`, `v1:w:done`, `v1:w:skip` |
| Anonymity / multiple | `v1:w:anon`, `v1:w:multi` |
| Target toggle / page | `v1:w:tgt:<chat_id>`, `v1:w:tgtp:<page>` |
| Schedule kind | `v1:w:kind:daily` … `w:kind:none` |
| Weekday toggle | `v1:w:wd:<1..7>` |
| Option remove | `v1:w:opt:<index>` |
| Confirm | `v1:w:confirm:<entity>_<id>` (existing) |

## 2. Architecture impact

- `bot/wizards/states.py`: add `chat_page` and weekday-selection helper state; no persistence.
- `bot/wizards/engine.py`: new events `WEEKDAY`, `PAGE`; `TOGGLE` reused for chats and binary steps;
  `_forward` unchanged.
- `bot/keyboards.py`: `wizard_keyboard(state, chats_page)`; truncation helper; exact 8×4 validation.
- `bot/handlers/commands.py`: `handle_callback` routes new wizard actions; keyboard attached to every
  prompt; text fallback unchanged.
- `bot/render.py`: `chat_button_label`, numbered preview, chat-name list.
- `bot/callbacks.py`: named builders; codec unchanged.

## 3. Data design

No schema change. Chat names come from `chats.title`; pagination is derived from `chats.list_all` with
limit/offset and `count`.

## 4. Behaviour details

- Targets: 6 buttons + Prev/Next + Done + Back/Cancel; selection persists in `state.targets`.
- Weekdays: 2 rows × 4 (Mon–Sun + placeholder) with ✅ markers; Done advances to time.
- Options: current list rendered in the message (not buttons); one Remove button per option, up to 8
  with pagination; Add another prompts for text through the existing TEXT event.
- Preview: numbered options, chat names, schedule summary, next five; Confirm/Back/Cancel buttons.

## 5. Test strategy

- Codec property loop for every action; 64-byte ceiling.
- Keyboard table tests: every step, 0/1/7/8/9 chats, long titles, 12 options.
- Engine: weekday toggles, target page changes, button-only path.
- Handlers: every callback, stale/foreign callback, text fallback.
- Integration: real SQLite, button-driven create, preview content.

## 6. Risks

| Risk | Mitigation |
|---|---|
| 64-byte overflow | ids/indices only; codec test |
| 8-row overflow (chats + nav + Done) | 6/page; layout test |
| Title truncation ambiguity | keep id in the label |
| Stale callbacks | TTL check + answer “expired” |
| Text fallback ambiguity | same engine events as buttons |
