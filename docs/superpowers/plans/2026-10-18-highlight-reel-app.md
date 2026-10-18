# Highlight Reel in the App Implementation Plan (part 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The highlight reel is made from the app, local and hosted: an Overview card shows how close the match is to unlocking it, Export gets a "Highlight reel" mode (moment menu, normal or Shorts, portrait or landscape), a `highlight_reel` job renders it, the run lands in export history with YouTube upload, and a share link plays it.

**Architecture:** Server: one readiness route (pure over the project and its audit docs, works hosted), one submit route, and the `highlight_reel` job body, which pulls trims down when hosted, calls `reel_inputs.make_reel`, writes a YouTube sidecar, records an `ExportRun` and pushes the MP4. SPA: a pure `lib/highlightReel.ts` (menu, readiness wording, request body), an Overview card, and Export's fourth mode. Share: one more GET shape in the share allowlist and a Highlights view on the share shell.

**Tech Stack:** FastAPI, pytest; React + TypeScript (vitest, `pnpm typecheck` = `tsc -b`).

**Spec:** `docs/superpowers/specs/2026-10-18-highlight-reel-design.md` (section 4); part 1 plan `docs/superpowers/plans/2026-10-18-highlight-reel-engine.md`.

## Global Constraints

- Every user-facing change adds its What's new entry in the same PR (`whats-new` skill; `tests/test_whats_new.py`).
- The SPA builds with the primitives in `components/ui` only (PageHeader, Label, Stat, Chip, Button with one primary per view, Segmented, Field, Sheet, Menu); no arbitrary `text-[...]`; red only for brand, the one primary action, current position and focus.
- Every page's derivation lives in a pure `lib/*.ts` with tests; components map results to primitives.
- Hosted: no job without `hosted_compute` (the backend's `submit_allowed` covers a new kind); the submit route also declares `Depends(require_feature(Feature.hosted_compute))` so the refusal is a clean 403.
- A request never runs ffmpeg or pulls large files; the job does.
- `export_runs.load_log` drops a run it cannot validate: record the reel as `kind="match"` with a `match_video` artifact, never a new Literal value (older builds would lose the run from history).
- The share allowlists `_SHARE_PATH_RE` (GET) and `_SHARE_WRITE_ROUTES` stay separate; a new shape is GET only, strict filename, no wildcard.
- `ExportMode` changes in both `lib/exportPlan.ts` and `export_presets.py` together.
- Lolz moments are never on by default, in the UI as in the config.

## Review Focus

1. A hosted desktop mirror: trims are absent until pulled; readiness must not call that `trim_missing` (the request cannot see trims), and the job must pull the trim or the web rendition before `make_reel`, else it fails every stage. (Task 3 test `test_hosted_job_pulls_each_stages_clips_before_rendering`.)
2. Two renders at once: a second reel submit while one runs for the same shooter answers 409, not a duplicate job. (Task 2 test.)
3. A reel run in history on an older desktop build: it must still load (kind `match`, artifact `match_video`). (Task 3 test reads it back through `load_log`.)
4. A share viewer guessing other export files: only `*-highlights.mp4` of that match's shooters is reachable. (Task 6 test with a `.fcpxml` and a traversal attempt.)
5. A user who unticks every moment: the Render button is disabled with a reason, and the server refuses an empty list with 422. (Tasks 2 and 5.)

---

### Task 1: Readiness and menu route

**Files:**
- Modify: `src/splitsmith/highlights.py` (add `readiness_for_project`)
- Create: `src/splitsmith/ui/highlights_api.py`
- Modify: `src/splitsmith/ui/server.py` (include the router)
- Test: `tests/test_highlights_api.py`

**Interfaces:**
- Produces: `highlights.readiness_for_project(project: MatchProject, statuses: dict[int, StageStatus], audit_docs: Mapping[int, dict | None]) -> tuple[StageReadiness, ...]`: footage counted present when the primary has a beep and `processed["trim"]` (a request cannot see hosted trims; the job re-checks on disk and reports `trim_missing` then).
- Produces: `GET /api/shooters/{slug}/highlights` -> `{unlocked: bool, ready: int, total: int, stages: [{stage_number, state, proposals}], menu: [{kind, label, group, default}], cuts: ["normal","short"]}`, menu in `BEAT_ORDER`.

- [ ] Step 1: tests. (a) A project with one audited, seeded, proposal-free stage and one with two auto regions: `unlocked` false, stages `ready` and `proposals_left` with 2. (b) Every kind of `MOMENT_KINDS` appears once in `menu`, defaults flagged `default: true` exactly for `DEFAULT_MOMENTS`, lolz never default. (c) Hosted shape: audit docs passed in (not files) give the same answer. Use the `_project` builder pattern from `tests/test_reel_inputs.py` and FastAPI's `TestClient` through the existing local app fixture (find it with `grep -n "def client\|def app_client" tests/conftest.py`).
- [ ] Step 2: run, watch fail (`ModuleNotFoundError` / 404).
- [ ] Step 3: implement `readiness_for_project` (loop `project.stages`, `stage_readiness(n, statuses[n], audit_docs.get(n), has_video=primary is not None, has_footage=bool(primary and primary.beep_time is not None and primary.processed.get("trim")))`), and the route: load the project and audit docs the way the coach GET does in both modes (`state.shooter_project(slug)`, audit docs via `state.load_audit` per stage, statuses via `project.stage_statuses(root, audit_docs=...)`), labels from `reel_plan.MOMENT_LABELS`.
- [ ] Step 4: run, pass. Step 5: commit `feat(highlights): readiness and the moment menu over the API`.

### Task 2: The `highlight_reel` job and its submit route

**Files:**
- Modify: `src/splitsmith/ui/highlights_api.py` (POST route)
- Modify: `src/splitsmith/ui/server.py` (body registered beside `match_export` at ~5126)
- Modify: `src/splitsmith/ui/auto_sync.py:41,44` (`RENDER_KINDS`, `_UNSYNCED_JOB_KINDS`)
- Test: `tests/test_highlights_api.py`

**Interfaces:**
- Produces: `HighlightReelRequest(BaseModel, extra="forbid")`: `moments: list[MomentKind] = Field(min_length=1)`, `short: bool = False`, `upright: bool = True`, `force: bool = False`, `youtube_sidecar: bool = True`.
- Produces: `POST /api/shooters/{slug}/highlights` (dependency `require_feature(Feature.hosted_compute)`) -> the Job JSON; 409 `{code: "busy"}` when `state.jobs.find_active(kind="highlight_reel", shooter_slug=slug)` or a `match_export` / `render_upload` for the slug is active; 409 `{code: "locked", stages: [...]}` when not unlocked and not `force` (checked with `readiness_for_project` so the refusal is immediate, not a failed job).
- Produces: job body `_run_highlight_reel(handle: JobHandle, slug: str, request: dict) -> None`; args are plain JSON (`request.model_dump()`), so `job_journal` needs no entry.

- [ ] Step 1: tests: submit returns a job of kind `highlight_reel`; a second submit 409 `busy`; an empty `moments` 422; a locked match 409 `locked` naming the stage; `force` submits. Run the body with `make_reel` monkeypatched (record its kwargs): `moments`, `short`, `upright` reach it; `handle.set_result` gets `{filename, duration_seconds, degradations, left_out}`.
- [ ] Step 2: run, fail.
- [ ] Step 3: implement. Body: `handle.update(progress=0.05, message="Reading stages")`; materialize every stage's audit (`state.materialize_audit(slug, n)`, as `_run_match_export` at ~4571); hosted prefetch (Task 3); `make_reel(..., segment_cache=match_exports.render_segment_cache(output_config), config=Config.load(None).highlights)`; output `project.exports_path(root) / f"{stage_file_safe(project.name)}-highlights{'-short' if short else ''}.mp4"` (reuse the export naming helper the match export uses for its base name; find it with `grep -n "def .*file_base\|def export_base" src/splitsmith/export_naming.py`); `ReelOutcome.result is None` raises with the blockers named (a forced reel with nothing ready). Register in `register_job_bodies`; add `"highlight_reel"` to `RENDER_KINDS` and `_UNSYNCED_JOB_KINDS`. `tests/test_access_backstop.py` covers the hosted refusal automatically (it enumerates `bodies.kinds()`); run it.
- [ ] Step 4: run, pass (also `tests/test_access_backstop.py`). Step 5: commit `feat(highlights): the highlight_reel job`.

### Task 3: Hosted footage, history, YouTube sidecar, storage

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`_run_highlight_reel`)
- Modify: `src/splitsmith/reel_inputs.py` (`make_reel` returns the run's stage numbers in `ReelOutcome.stages`)
- Test: `tests/test_highlights_api.py`

**Interfaces:**
- Consumes: `audio.pull_trimmed_video(root, n, video, project=)` (`ui/audio.py:1250`), `audio.try_pull_web_trim(project, trim_path)` (`audio.py:986`), `audio.trimmed_video_path`, `_record_export_run(state, slug, run)` (`server.py:787`), `export_runs.ExportRun / ExportArtifact / new_run_id`, `export_storage.push_export_file(project, path)` (`export_storage.py:96`).
- Produces: `_prefetch_reel_footage(state, project, root) -> None` (hosted only: every stage's primary and each `beep_reviewed` secondary: `pull_trimmed_video`, else `try_pull_web_trim` on a mirror); the run recorded as `ExportRun(kind="match", stage_numbers=outcome.stages, formats=["mp4"], artifacts=[ExportArtifact(filename, "match_video"), ExportArtifact(sidecar, "sidecar")])`.

- [ ] Step 1: tests: (a) `test_hosted_job_pulls_each_stages_clips_before_rendering`: hosted state stub, `pull_trimmed_video` and `make_reel` monkeypatched, assert pulls happen for the primary and the reviewed secondary only, before `make_reel`; (b) the recorded run reads back through `export_runs.load_log` with kind `match` and a `match_video` artifact; (c) a `-youtube.json` sidecar exists beside the MP4 with title `"<match> highlights"` and is accepted by `youtube_api`'s upload precondition (call the function the route uses to read the sidecar); (d) hosted: `push_export_file` is called with the MP4 and the sidecar.
- [ ] Step 2: run, fail.
- [ ] Step 3: implement. Sidecar: use the writer the match export uses (`grep -n "def write_youtube_sidecar\|def youtube_sidecar" src/splitsmith/youtube_sidecar.py`), title `f"{project.name} highlights"`, description: the end card's lines (`reel_inputs.end_lines`) and "Made with splitsmith", tags `["IPSC", "practical shooting", "highlights"]`, privacy default `unlisted`. Upright reels add `#Shorts` to the title when `short`.
- [ ] Step 4: run, pass. Step 5: commit `feat(highlights): hosted footage, history and the YouTube sidecar for the reel`.

### Task 4: SPA model and the Overview card

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/highlightReel.ts`, `lib/highlightReel.test.ts`
- Modify: `lib/api.ts` (types `HighlightReadiness`, `HighlightMenuItem`; `getHighlights(slug)`, `submitHighlightReel(slug, body)`)
- Create: `components/overview/HighlightReelCard.tsx`
- Modify: `pages/Home.tsx` (render the card between `StatStrip` and the table, lead shooter)
- Modify: `lib/jobLabels.ts` (label "Highlight reel"; check the file exists and how kinds are labelled first)

**Interfaces:**
- Produces (pure): `readinessLine(r: HighlightReadiness): string` ("5 of 8 stages ready"; "Ready"); `blockerText(stage): {text, to}` (`not_audited` -> "Audit shots", `/audit?stage=N`; `breakdown_not_opened` -> "Open in Breakdown"; `proposals_left` -> "N proposals left in Breakdown"; `trim_missing` -> "Trim again"); `defaultSelection(menu): Set<MomentKind>`; `groupMenu(menu): {default, stat, lolz}`; `reelBody(selection, cut, orientation, force): HighlightReelBody`.
- [ ] Step 1: vitest for every pure function (wording table, grouping keeps `BEAT_ORDER`, defaults never include lolz).
- [ ] Step 2: fail. Step 3: implement lib, api wrappers (spread payloads, never list fields: `api.exportBodies.test.ts` rule), the card: locked shows the readiness line and up to three blocker links (the rest as "and N more"); unlocked shows "Ready" and a default-variant Button "Make highlight reel" linking to `/export?mode=reel`. Hidden on share pages and for a match with no audited stage.
- [ ] Step 4: `pnpm test`, `pnpm typecheck`, `pnpm lint`. Step 5: commit `feat(highlights): the Overview card`.

### Task 5: Export's "Highlight reel" mode

**Files:**
- Modify: `lib/exportPlan.ts` (`ExportMode` += `"reel"`), `src/splitsmith/export_presets.py` (`ExportMode` Literal += `"reel"`)
- Create: `components/export/ReelPanel.tsx`
- Modify: `pages/Export.tsx` (`selectMode`, the Segmented at ~936: "Highlight reel" option; `?mode=reel` honoured on load; reel mode hides the stage table and the Look groups that do not apply, renders `ReelPanel`)
- Modify: `components/export/ExportHistory.tsx` only if a `-highlights` run needs its own name (check how runs are titled first)
- Test: `lib/highlightReel.test.ts`, a page test in the Export page's test file (open the groups with its `openGroups` helper)

**Interfaces:**
- `ReelPanel` props: `{slug, readiness, onSubmitted(job)}`; contents: blockers (as on the card) with a "Render anyway" switch when locked (`force`), the moment toggles in three groups (Default, Stats, Just for fun) as `Chip`-style toggles, `Segmented` Cut (Normal, Shorts), `Segmented` Format (Portrait, Landscape), and the page's one primary Button "Render reel", disabled with a reason when no moment is ticked or the match is locked without the switch.
- [ ] Step 1: tests: switching to reel mode shows the panel; defaults ticked; unticking all disables Render with its reason; Render posts `reelBody(...)`; `ExportPresetBody` accepts `mode: "reel"` (pytest) and a preset saved with it loads.
- [ ] Step 2: fail. Step 3: implement; the job is polled with `api.pollJob` like the bundle; the result line names `left_out` stages when forced.
- [ ] Step 4: `pnpm test && pnpm typecheck && pnpm lint`, pytest for presets. Step 5: commit `feat(highlights): Export's highlight reel mode`.

### Task 6: The reel on a share link

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`_SHARE_PATH_RE` ~1347: one alternative `shooters/[^/]+/exports/file/[A-Za-z0-9._-]+-highlights(-short)?\.mp4`; and `shooters/[^/]+/highlights/latest`)
- Modify: `src/splitsmith/ui/capabilities.py` (map both shapes to the read capability, as `videos/stream` is)
- Modify: `src/splitsmith/ui/highlights_api.py` (`GET /api/shooters/{slug}/highlights/latest` -> `{filename, short, upright, finished_at} | 404`, newest reel run from the export log whose file is available)
- Create: SPA `pages/share/ShareHighlights.tsx`, route `share/:token/highlights` in `App.tsx`, a "Highlights" entry on the share shell's nav when `latest` answers
- Test: `tests/test_share_highlights.py`, vitest for the nav rule

- [ ] Step 1: tests: through a share token, `latest` and the `-highlights.mp4` file answer 200; `x.fcpxml`, `x.mp4` (not highlights), `..%2f` traversal and another match's shooter answer the share surface's opaque 404; a POST to the file is 404.
- [ ] Step 2: fail. Step 3: implement; the share page is a `PageHeader` and a `<video controls playsInline>` at the reel's aspect.
- [ ] Step 4: run pytest and vitest. Step 5: commit `feat(highlights): the reel on share links`.

### Task 7: What's new, frames, the whole branch

- [ ] Step 1: What's new entry via the `whats-new` skill (id `highlight-reel`, date of the PR).
- [ ] Step 2: run the app locally against the demo seeder (`scripts/seed_demo_match.py ~/.claude-tmp/demo-match --media`, mark breakdowns with the part 1 scratch script, `SPLITSMITH_AUTO_SYNC=0 uv run splitsmith ui --project ... --port 5174`), render a reel from Export, screenshot the Overview card locked and unlocked and the reel panel, and play the result.
- [ ] Step 3: full suites: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest`, `pnpm test`, `pnpm typecheck`, `pnpm lint`, ruff, black.
- [ ] Step 4: one whole-branch review (fresh reviewer), naming the Review Focus items.
