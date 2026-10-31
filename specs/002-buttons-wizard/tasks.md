# Tasks: SPEC-002 — Buttons-first poll wizard

Each task is independently verifiable and paired test-first. Check a task only when its acceptance
passes locally and in CI.

## Phase 1 — Codec and keyboards

- [x] **T-201** (test) Callback keys for every wizard action.
  - Files: `tests/unit/test_callbacks.py`
  - Cases: round-trip each new key; ≤64 bytes; unknown action rejected.
  - Covers: FR-213
- [x] **T-202** (feat) Named callback builders for wizard actions.
  - Files: `bot/callbacks.py`
  - Covers: FR-213
- [x] **T-203** (test) Wizard keyboard layout.
  - Files: `tests/unit/test_keyboards_wizard.py`
  - Cases: every step has Back/Cancel; 8×4 limits; 0/1/6/7/13 chats; title truncation; selected markers; 12 options.
  - Covers: FR-201, FR-204, FR-205, FR-207, FR-208, FR-209, FR-210, FR-214
- [x] **T-204** (feat) `wizard_keyboard` and labels.
  - Files: `bot/keyboards.py`
  - Covers: FR-201, FR-204, FR-205, FR-207, FR-208, FR-209, FR-210, FR-214

## Phase 2 — Engine and rendering

- [ ] **T-205** (test) Engine button events.
  - Files: `tests/unit/test_wizard_engine.py`
  - Cases: weekday toggle/clear; target page; toggles; option removal; button-only path.
  - Covers: FR-202, FR-205, FR-206, FR-209, FR-210, FR-212
- [ ] **T-206** (feat) Add `WEEKDAY`/`PAGE` events and state fields.
  - Files: `bot/wizards/{states,engine}.py`
  - Covers: FR-205, FR-209
- [ ] **T-207** (test) Numbered preview and chat-name labels.
  - Files: `tests/unit/test_render.py`
  - Cases: 3 long options numbered; names instead of ids; 4,096 cap preserved.
  - Covers: FR-211, FR-215
- [ ] **T-208** (feat) Renderers.
  - Files: `bot/render.py`
  - Covers: FR-211, FR-215

## Phase 3 — Handlers

- [ ] **T-209** (test) Callback routing.
  - Files: `tests/unit/test_handlers.py`
  - Cases: each action; stale TTL; foreign admin; text fallback; button-only create.
  - Covers: FR-202, FR-206, FR-212
- [ ] **T-210** (feat) Attach keyboards and route callbacks.
  - Files: `bot/handlers/commands.py`
  - Covers: FR-201–FR-212
- [ ] **T-211** (test) Integration: button-only creation on SQLite.
  - Files: `tests/integration/test_wizard_buttons.py`
  - Covers: FR-202, FR-205, FR-210, FR-211

## Phase 4 — Docs and release

- [ ] **T-212** (docs) Update README/runbook wording and CHANGELOG.
- [ ] **T-213** (test) Full gates + CI; live check with the test bot.
- [ ] **T-214** (docs) Reconcile and request G3/G4.

## Progress log

| Date | Task | Status | Notes |
|---|---|---|---|
| 2026-10-31 | G1 | closed | “all recommended”, keep text shortcuts |
