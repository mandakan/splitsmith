# Triage Follow-ups (#823) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the non-deferred follow-ups from issue #823 (slice-4 final review + staging E2E) in one branch.

**Architecture:** Four independent tasks: a cheap triage summary endpoint + resolved threshold in the payload (backend), the SPA consuming both, desktop Audit surfacing the flag with clear-on-save, and a tests/cleanup sweep.

**Tech Stack:** FastAPI (src/splitsmith/ui/server.py), React SPA (src/splitsmith/ui_static, pnpm only), pytest + vitest.

## Global Constraints

- Worktree ~/.claude-tmp/wt-sync-spec, branch fix/823-triage-followups (off 162fc38).
- Single ASCII dash "-" in new copy/comments; never em dash, never "--".
- Per-task verification runs SCOPED test files only (named per task); full suites run once in Task 4's gate step. This is a standing user rule.
- Backend status/threshold values are authoritative; the SPA never recomputes or hardcodes them.
- WCAG 2.2 AA; 44px targets; status never color-only. pnpm only. No new deps. `git checkout -- uv.lock` before every commit.
- Excluded by design (noted in #823): malformed needs_attention hardening (trust-the-producer pattern stands); stale-base LWW race gets a code comment only (Task 4), not a behavior change.

---

### Task 1: Triage summary endpoint + resolved threshold in TriageResponse

**Files:**
- Modify: `src/splitsmith/ui/server.py` (TriageResponse model ~3875; `_build_triage_response` ~12557; new GET below `/api/match/triage` ~12623)
- Test: `tests/test_triage_api.py`

**Interfaces:**
- Produces: `TriageResponse` gains `beep_low_confidence_threshold: float` - the RESOLVED per-project value, obtained exactly the way `get_hitl_queue` does (`resolved.settings.beep_low_confidence_threshold`, see server.py ~8077; reuse the same resolution helper, do not re-derive).
- Produces: `GET /api/match/triage/summary` -> `{"flagged_count": int}` (new model `TriageSummaryResponse`). Implementation must NOT compute anomalies or engine shots: enumerate shooters, bulk `state.load_audit_docs(slug)` (hosted) / per-stage `load_audit` fallback (local), count docs where `needs_attention.flagged` is truthy. No status walk needed.

**Steps:**
- [ ] Failing tests: (a) `test_triage_carries_resolved_threshold` - triage GET includes the field, equal to the project's resolved setting (override it in the test project config to a non-default like 0.5 and assert 0.5 comes back); (b) `test_triage_summary_counts_flags_only` - flag one stage via the attention endpoint, `GET /api/match/triage/summary` == {"flagged_count": 1}; after unflag == 0. Run: `uv run pytest tests/test_triage_api.py -q` -> new tests FAIL.
- [ ] Implement both (threshold field + summary endpoint + `_count_flagged` helper shared with `_build_triage_response`'s flagged_count so ONE counting rule exists).
- [ ] `uv run pytest tests/test_triage_api.py -q` green; `uv run ruff check src/splitsmith/ui/server.py tests/test_triage_api.py && uv run black --check <same>`.
- [ ] Commit: `feat(triage): summary endpoint and resolved threshold in payload (#823)`

### Task 2: SPA consumes summary endpoint + payload threshold

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (TriageResponse type + `getTriageSummary` method), `src/components/match/MatchShell.tsx` (both fetch sites swap `getTriage` -> `getTriageSummary`), `src/pages/Triage.tsx` (drop `BEEP_LOW_CONFIDENCE` constant, use `data.beep_low_confidence_threshold`)
- Test: `src/pages/Triage.test.tsx` (fixture gains the field; add a case where threshold 0.5 hides the 0.65-confidence pill), MatchShell tests (mock `getTriageSummary`)

**Interfaces:**
- Consumes Task 1's `TriageSummaryResponse {flagged_count}` and the new TriageResponse field.

**Steps:**
- [ ] Failing tests first (threshold-driven pill case; MatchShell mocks getTriageSummary). Run scoped: `pnpm exec vitest run src/pages/Triage.test.tsx src/components/match/MatchShell.test.tsx` (locate the real MatchShell test filenames first).
- [ ] Implement; delete the hardcoded 0.95 and its comment entirely (no fallback constant - the field is required in the type).
- [ ] Scoped vitest green + `pnpm typecheck` + scoped eslint.
- [ ] Commit: `refactor(ui): triage summary poll and payload-driven confidence threshold (#823)`

### Task 3: Desktop Audit surfaces the flag + clear-on-save

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`put_stage_audit` ~10373), `src/splitsmith/ui_static/src/pages/Audit.tsx` (flag banner near the stage header; find where the audit doc payload is already in state)
- Test: `tests/test_triage_api.py`, `src/pages/Audit.test.tsx` if it exists (else the existing Audit test file - locate first)

**Interfaces:**
- Server: in `put_stage_audit`, AFTER stamping event ids: if the incoming payload's `audit_events` contain a `save` kind AND the STORED doc (or incoming payload) has `needs_attention.flagged` truthy, call `_set_needs_attention(payload, flagged=False)` - a desktop full-audit save resolves the flag (plan decision 4 extended to the save path). Preserve the object form (LWW clear), never pop the key.
- SPA: when the loaded audit doc has `needs_attention.flagged`, render an amber StatusPill row "Flagged for desktop" + the note text near the stage header (non-color-only, `role="status"`). No new actions - saving clears it (server-side), unflag stays on the triage page.

**Steps:**
- [ ] Failing tests: backend `test_desktop_save_clears_flag` (flag a stage, PUT a doc containing a save event, GET shows flagged False with fresh updated_at); SPA banner render test with a flagged doc fixture.
- [ ] Implement both sides.
- [ ] Scoped runs only: `uv run pytest tests/test_triage_api.py tests/test_ui_server.py -q` (put_stage_audit is shared surface) + scoped vitest for the Audit test file + `pnpm typecheck`.
- [ ] Commit: `feat(audit): desktop surfaces triage flag; audit save resolves it (#823)`

### Task 4: Pin tests + cleanups + gates + PR

**Files:**
- Modify: `tests/test_mirror_read_only.py` (parametrized gate-boundary cases: POST `shooters/x/stages/1/attention/extra` -> 403, PATCH attention -> 403, POST `audit/accept/` trailing slash -> 403), `src/splitsmith/ui_static/src/components/audit/AnomalyChips.tsx` (drop the wrapper div - key the button/div branches directly), `src/splitsmith/ui_static/src/pages/Audit.tsx` or wherever `buildAuditJson` lives (one comment block documenting the stale-base LWW race and why it is accepted, referencing #823).
- Test: existing files above + `AnomalyChips.test.tsx` (assert chip count == DOM children, no wrapper).

**Steps:**
- [ ] Failing/added tests, implement, scoped runs (`tests/test_mirror_read_only.py`, AnomalyChips vitest).
- [ ] Commit: `test(triage): gate boundary pins; chip DOM cleanup; document LWW race (#823)`
- [ ] END-OF-BRANCH GATES (the one full pass): `uv run ruff check . && uv run black --check . && uv run pytest -n auto -q` (compare failures only against the known slim-ffmpeg set); `pytest -m docker -n0` ONLY IF server.py sync-adjacent code changed (Task 1/3 touch triage endpoints - yes, run it); SPA `pnpm test && pnpm typecheck && pnpm exec eslint src`; dash grep on the branch diff.
- [ ] Push, `gh pr create` (body: per-item mapping to #823, verification evidence, note the two deliberately-deferred items stay open), watch CI green, merge (`gh pr merge --merge --delete-branch`), comment on #823 with what shipped vs what remains, close #823 if all its actionable items shipped (leave open with a comment if the deferred items should keep it open - they should: leave it OPEN, retitle comment accordingly).
