# Stage details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One page per shooter to type or paste each stage's time, scores and shooting order; the app leads there when data is missing; the footage sort uses the order to place clips when clocks fail.

**Architecture:** Pure scoring and provenance rules in a new `stage_details.py`; one PUT route writes a shooter's rows; the sync merge gains a per-stage unit. The SPA gets a Stage details page (pure `lib/stageDetails.ts` for paste and derivation), a notice on Overview, a typed field in Audit. The sort engine gains recording-order ranking, stage numbers in names, a camera's shooter, and a conservative order alignment.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-22-stage-details-design.md`

## Global Constraints

- ASCII only in code, copy and docs; no dash punctuation in UI copy.
- `uv` only; no new dependencies.
- Black/ruff line length 110.
- Wire types generated: `uv run python scripts/export_openapi.py` then `pnpm gen:api`; aliases in `lib/api.ts`.
- SPA on primitives (`ui-budget.md`): one primary per view, `Label` 1-3 words, `numeral` on numbers, `Table` primitives.
- A request never runs ffmpeg (nothing here needs it).
- Footage sort stays local only; Stage details works local and hosted.
- Scoring: A 5; C 3 minor / 4 major; D 1 minor / 2 major; M, NS, Proc -10 each; points floor 0; HF = points / time rounded to 4 decimals.

## Review Focus

1. A scoreboard re-sync after the user typed values: typed time and typed score fields survive, blanks fill. (Task 1 test.)
2. A hosted edit pulled onto the desktop: the stage's details win per the three-way rule, beeps still merge. (Task 2 test.)
3. Paste from a non-Swedish SSI page or with a missing column: English headers map, missing columns leave cells untouched. (Task 4 test.)
4. A camera with one extra clip (walkthrough): no wrong proposal, the stretch stays open. (Task 6 test.)
5. A camera the clock already places: the order pass changes nothing. (Task 6 test.)

---

### Task 1: Scoring rules and data model

**Files:**
- Create: `src/splitsmith/stage_details.py`
- Modify: `src/splitsmith/match_project.py` (`StageScorecard`, `StageEntry`, `MatchProject`, `merge_stage_times`)
- Test: `tests/test_stage_details.py`

**Interfaces (produces):**
- `StageScorecard.manual: list[str] = []` (fields the user typed), `StageScorecard.derived: list[str] = []` (fields computed by `derive`).
- `StageEntry.shot_order: int | None = None`.
- `MatchProject.power_factor: Literal["minor", "major"] | None = None`.
- `stage_details.SCORE_FIELDS` (the ten scorecard value fields).
- `stage_details.power_factor_for(division: str | None, override: str | None) -> Literal["minor","major"]` ("major" when override says so, or division contains "major" case-insensitive).
- `stage_details.points_from_hits(card, pf) -> float | None` (None unless A, C, D, M, NS, Proc all known).
- `stage_details.derive(card: StageScorecard, time_seconds: float, pf) -> StageScorecard`: fills `stage_points` then `hit_factor` only when the field is None or listed in `derived`, and the inputs exist; clears a previously derived field whose inputs went away; never touches a field in `manual`; updates `derived`.
- `stage_details.disagreements(card, time_seconds, pf) -> list[str]`: plain-sentence warnings when a non-derived points or HF value differs from the computed one (points exact, HF to 4 decimals).
- `stage_details.scorecard_order(project) -> list[int]`: stage numbers sorted by `scorecard_updated_at` (stages without one left out).

- [ ] Tests first: `power_factor_for("Production Optics", None) == "minor"`, `("Classic Major", None) == "major"`, override wins; points for 5A 4C minor = 37, 2A 7C 1M minor = 21, major C/D values, floor at 0; `derive` fills points and HF (37 / 12.68 = 2.918), does not overwrite a manual or scoreboard value, recomputes a derived one when A changes, clears it when an input is removed; `disagreements` wording for a pasted 23 against computed 21; `merge_stage_times` keeps a manual time and a manual `alphas` and fills a blank `charlies`; a stage with only scoreboard values is replaced as today; `scorecard_order` ordering.
- [ ] Run, see them fail.
- [ ] Implement. `merge_stage_times` builds the new card from the result, then copies back every field in the old card's `manual` (and keeps `manual`), then runs `derive` with the project's power factor (division from `competitor_division`).
- [ ] Run `uv run pytest -n0 tests/test_stage_details.py tests/test_match_project*.py -q`, all pass.
- [ ] Commit `feat: scoring rules and stage details data`.

### Task 2: The write route and the sync unit

**Files:**
- Modify: `src/splitsmith/ui/stages_api.py` (new route), `src/splitsmith/sync/merge.py`
- Test: `tests/test_stage_details_api.py`, the existing sync merge test file (`tests/test_sync_merge*.py`)

**Interfaces:**
- Consumes Task 1.
- `StageDetailsRow(BaseModel)`: `stage_number: int`, `time_seconds: float | None`, `shot_order: int | None`, one optional field per `SCORE_FIELDS` entry, `manual: list[str]`.
- `StageDetailsRequest`: `rows: list[StageDetailsRow]`, `power_factor: Literal["minor","major"] | None`.
- `PUT /api/shooters/{slug}/stage-details` -> the project JSON. 400 on a duplicate `shot_order` or an unknown stage number; a time <= 0 means clear (as `set_stage_time`). A row's `time_seconds` set and different from stored marks `time_seconds_manual=True`. Score fields listed in `manual` are written and kept manual; others are left as stored. Then `derive` per stage. One `project.save`.
- `GET /api/shooters/{slug}/stage-details` -> `StageDetailsView`: per stage the stored values, `manual`, `derived`, `disagreements`, the scorecard-derived order; plus `power_factor` (effective), `division`, `ssi_url` (participant link or match link or null), and `others: list[{slug, name, order}]` for "Copy order from".
- `merge.py`: `_stage_details_unit(stage: dict) -> dict` = `time_seconds`, `time_seconds_manual`, `shot_order`, `scorecard`; `merge_project_doc` resolves it per stage number through `_resolve_unit` and records a conflict like the beep unit; `power_factor` is one top-level unit.

- [ ] Tests first: PUT round-trip (time, order, manual alphas, derived points present in the response); duplicate order 400; clearing a time; GET lists disagreements and the participant link `https://shootnscoreit.com/event/participant/23/512019/` for `selected_competitor_id=512019`, the match link when the id is missing; merge: remote-only change to a stage's details wins, both changed resolves by timestamp with a conflict, a beep change on the same doc still merges.
- [ ] Check the participant link's `23` against a second SSI match (SSI Scoreboard tool `get_match` on any other match, then open one competitor link); if it varies, derive it and say so in the PR.
- [ ] Implement; run `uv run pytest -n0 tests/test_stage_details_api.py tests/test_sync_merge*.py -q`.
- [ ] Export OpenAPI, `pnpm gen:api`, aliases `StageDetailsView`, `StageDetailsRequest` in `lib/api.ts`, client functions in `lib/api/stages.ts`.
- [ ] Commit `feat: stage details route and sync merge unit`.

### Task 3: Next step and the notice's data

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`NextStep`, `_next_step_from_statuses`, its caller), `src/splitsmith/ui_static/src/lib/matches.ts` (Continue card wording)
- Test: existing next-step tests (`tests/test_*next_step*.py` or where `_next_step_from_statuses` is tested), `lib/matches.test.ts`

**Interfaces:**
- `NextStep.kind` gains `"details"`.
- `_next_step_from_statuses(per_shooter, stage_names, video_total, untimed: list[str] = [])`: `untimed` is the slugs, in match order, with a non-skipped stage at `time_seconds <= 0`. With `video_total == 0` and `untimed`, `details` for the first. A `partial` stage (footage, no time) yields `details` for its shooter instead of `footage`.
- Card copy: "Fill in stage details for <name>".

- [ ] Tests first for both rules and that `audit` still beats `details`.
- [ ] Implement; run the touched tests.
- [ ] Commit `feat: next step names missing stage details`.

### Task 4: Stage details page, paste, notice, Audit field

**Files:**
- Create: `ui_static/src/lib/stageDetails.ts` (+ test), `ui_static/src/pages/StageDetails.tsx` (+ test)
- Modify: `components/match/navItems.tsx`, `App.tsx`, `pages/Home.tsx` (notice), `components/StageTimeSection.tsx` (typed field first), `data/whats_new.json`

**Interfaces:**
- `parsePastedResults(text: string): { rows: PastedRow[]; unused: string[] }`: tab-separated, else runs of 2+ spaces; header row required; headers mapped case-insensitively: Station/Stage, HF, TID/Time, PTS/Points, A, C, D, M, PROC, NS (VRF ignored); stage from the first cell's leading number; decimal comma or point.
- `pointsFromHits`, `deriveRow`, `disagreement` mirroring Task 1 (same test numbers).
- `notice(view-like input): { slugs: string[] } | null` from the project list Home already has (stages with no time, not skipped).
- Page: `match/:matchId/stage-details/:slug`, `stage-details` -> `DefaultShooterRedirect base="stage-details"`; nav row "Stage details" in Prepare after Footage. Table per the mockup; Order number field plus drag; "Copy order from <name>" fills cells only; paste card with link and three steps; Discard and Save (primary). Save blocked while an order is duplicated.
- Home: the notice from the spec (one line naming shooters, the reason, button "Fill in for <first>").
- `StageTimeSection`: a seconds field and Save first (existing `set_stage_time` route), "Pick the end on the waveform" secondary, link to Stage details. Moved onto the primitives; drop it from the grandfathered list in `ui-budget.md`.
- What's new: `stage-details` entry ("Type in stage times and scores", where to find it).

- [ ] Tests first: parser on the user's Black Handgun table (12 rows, Swedish headers, decimal commas; row 1 gives time 12.68, points 37, HF 2.918), English headers, a missing column, an unknown stage; derivation numbers; page: paste fills rows unsaved, Save sends manual fields, Copy order fills without saving, duplicate order blocks Save; Home shows the notice and its link; Audit's typed time posts.
- [ ] Implement; `pnpm typecheck && pnpm lint && pnpm vitest run` from `ui_static`.
- [ ] Commit `feat(ui): stage details page`.

### Task 5: Sort inputs: ranking, stage names, camera shooter

**Files:**
- Modify: `src/splitsmith/footage_sort.py`, `src/splitsmith/ui/footage_sort_api.py`, `pages/FootageSort.tsx`, `lib/footageSort.ts`
- Test: `tests/test_footage_sort.py`, `tests/test_footage_sort_api.py`, `lib/footageSort.test.ts`, `pages/FootageSort.test.tsx`

**Interfaces:**
- `SortClip.modified: datetime | None` (file mtime, filled by the scan).
- `recording_rank(clips: Sequence[SortClip]) -> tuple[list[SortClip], RankEvidence] | None`: one evidence kind for the whole camera, the first every clip has: `"clock"` (start, when the camera is not no_timestamps), `"name_time"` (PXL, VID_datetime, datetime), `"name_number"` (IMG number; GoPro file number then chapter), `"modified"`. None when no kind covers every clip.
- `stage_in_name(filename: str) -> int | None`: `stage[_ -]?0*(\d+)`, `\bst0*(\d+)\b`, `\bs0*(\d+)\b`, case-insensitive, token-bounded.
- `CameraShooter(BaseModel)`: `camera_key`, `shooter`. Stored on the sort record (`camera_shooters: list[CameraShooter]`), sent through `DecisionsRequest`, passed to `propose(camera_shooters=...)`.
- `shooter_from_folder(folder: str, shooters: Sequence[ShooterScorecards]) -> str | None`: accent- and case-folded whole-word match on full name, first name or slug; None on zero or two matches.
- `ShooterScorecards.order: list[int]` (`shot_order`, else `scorecard_order`) and `covered: list[int]` (stages with footage in the project); filled in `_shooters`.
- `SortCamera.shooter: str | None`, `SortCamera.shooter_source: Literal["user","clip","folder"] | None`.
- SPA: a shooter `select` on each camera row in the anchor cameras block, labelled "Whose camera"; "from the folder name" hint when the source is folder; change writes decisions.

- [ ] Tests first: ranking per scheme (PXL times, IMG numbers, GoPro GX010012 before GX020012 before GX010013), a camera mixing schemes ranks by the common kind, `stage_in_name` positives and negatives ("stage_3.mp4", "S03", "IMG_3" is not a stage, "best3" is not); folder match ("Henrik", "henrik berg", "HENRIK_clips"), two matches give None; camera shooter precedence user > clip > folder; API round-trips `camera_shooters`.
- [ ] Implement; run the sort tests.
- [ ] Commit `feat: sort learns recording order and whose camera`.

### Task 6: Order alignment and reasons

**Files:**
- Modify: `src/splitsmith/footage_sort.py`, `pages/FootageSort.tsx` (reason copy), `lib/footageSort.ts`, `data/whats_new.json`
- Test: `tests/test_footage_sort.py`, `lib/footageSort.test.ts`

**Interfaces:**
- `ClipReason` gains `order_rank: int | None` (1-based in the camera), `order_position: int | None` (1-based in the shooter's order), `order_evidence: RankEvidence | None`, `issue` gains `"needs_order"`.
- `_order_pass(proposals, by_camera, camera_shooter, shooters) -> None`, run after `_apply_overrides` and before `_group_runs`. For each camera with a known shooter whose clock is `needs_anchor` or `no_timestamps`: rank (Task 5); fixed points are clips with a shooter and stage already (user or engine); between consecutive fixed points (and the ends), the open stages are the shooter's order positions strictly between the fixed points' stages, minus `covered`; when the stretch's undecided clip count equals the open stage count, propose clip i -> stage i at `needs_you`; otherwise leave. A clip whose name has a stage number is proposed that stage at `medium` before the pass and counts as a fixed point. A shooter with no order: set `issue = "needs_order"` on the camera's undecided clips.
- Reason copy (SPA): "<nth> clip from this camera by <evidence>, and stage NN is the <nth> stage <name> shot. Its clock does not match the scoreboard, so this is a suggestion." and for `needs_order`: "Set the order <name> shot the stages to place these clips." with a link to Stage details.
- What's new: edit the Task 4 entry or add `sort-footage-order` ("Place clips by shooting order").

- [ ] Tests first: equal counts map one to one; an extra clip leaves its stretch empty; a user decision in the middle splits stretches and both sides resolve; covered stages are skipped; a trusted camera is untouched; proposals are `needs_you`, never checked by default; `needs_order` without an order; reason fields set. Confirm each new test fails with `_order_pass` disabled.
- [ ] Implement; run sort tests and SPA tests.
- [ ] Commit `feat: sort places clips by shooting order`.

### Task 7: Verify and ship

- [ ] Full `uv run pytest -q` (no pnpm build while it runs), SPA `pnpm typecheck && pnpm lint && pnpm vitest run && pnpm build`, ruff, black.
- [ ] Local demo (`scripts/seed_demo_match.py --media`, `SPLITSMITH_AUTO_SYNC=0`, scratch `SPLITSMITH_HOME`): paste the Black Handgun table into Stage details, screenshot; the Overview notice; Audit's field; send screenshots to the user.
- [ ] One review pass (sonnet subagent, worktree) naming the claims: manual fields survive `merge_stage_times` and the sync merge; derived values never overwrite scoreboard or typed values; the order pass never proposes at `high` or touches a placed camera; each new test fails against the pre-change code.
- [ ] PR with a conventional title, `gh pr checks --watch`, merge only when the user says so.
