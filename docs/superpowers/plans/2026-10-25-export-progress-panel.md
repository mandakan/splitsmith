# Export progress panel and the export limit: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One panel on the Export page that shows a running export's time left
and every part's status and progress, with cancel and retry; and a hosted limit
on how many exports run at once.

**Architecture:** Every job an export queues carries an `export_id` (a
ContextVar, like `current_match_id`, written to the job row and re-set by the
worker), so an export is a query, not a new table. A job publishes its own
parts (`Job.parts`) through its handle; a pure estimates module turns parts plus
the user's finished history into time left. New `/api/me/exports/...` routes
serve the panel; an `export_slot` guard on every export route enforces the
limit. The SPA polls one route.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy + Alembic, Procrastinate (hosted),
React + TypeScript (SPA), ffmpeg `-progress`.

**Spec:** `docs/superpowers/specs/2026-10-25-export-progress-panel-design.md`

## Global Constraints

- Code checks a limit value, never a tier name.
- No new dependencies.
- Desktop and local mode: no limit, no check, no 409 ever.
- The default limit is 1 on every tier; a per-user override wins when set.
- Over the limit: HTTP 409, `detail = {"code": "export_limit", "running": <ExportSummary>}`; nothing waits in line.
- An export counts from its first queued job until no job of it is pending or running.
- A grid and its stage jobs are one export; trims queued by the audit chain are not exports.
- A first estimate with no history is labelled approximate ("about"); a part past its estimate reports `overrun`, never a negative or frozen time.
- The SPA's wire types are generated: after a route or model change run `uv run python scripts/export_openapi.py` then `pnpm gen:api` in `src/splitsmith/ui_static`, commit both.
- A user-facing change adds its What's new entry (`whats-new` skill).
- PR titles are conventional commits.
- Hosted request-path rules in `.claude/rules/hosted.md` apply: no per-row awaits on a poll route.

## Plan rulings (deviations from the spec, decided here)

- **Built-in estimates are fixed seconds per part, not seconds per footage-second.** The bench never recorded footage length per stage, so there is nothing to divide by. Scaling by footage applies once there is history. Cost if wrong: first estimates are rougher on very long or very short stages.
- **Retry on a failed grid stage re-queues the grid job only.** The grid job reuses every stored stage and renders the missing one itself. That is one job, about one stage's time, and it stays inside the same export. Cost if wrong: the retried stage renders on the parent's worker instead of a fresh one.
- **A standalone YouTube upload from an export history row is not an export.** The spec's list does not name it. Only a chained upload counts, as part of its export.

## Review Focus

1. Two export starts in the same second from two devices at limit 1: exactly one is admitted (Task 6, `test_two_concurrent_starts_admit_one`).
2. A job queued from inside a running export (the chained YouTube upload, a grid stage the parent re-queues) carries the same `export_id` in both backends (Task 1, `test_a_chained_submit_inherits_the_export`).
3. An export running in match A blocks the Export button in match B, and the note links to match A's export (Task 7, `test_active_lists_exports_across_matches`; Task 8 page test).
4. A part that runs past its estimate: time left is never negative, the part reports `overrun` (Task 5, `test_a_part_past_its_estimate_is_overrun_not_negative`).
5. Desktop starts many exports at once with no 409 (Task 6, `test_local_mode_has_no_limit`).

---

### Task 1: Export identity on every job

**Files:**
- Modify: `src/splitsmith/ui/context.py` (add the ContextVar)
- Modify: `src/splitsmith/ui/jobs.py` (`Job`, `JobRegistry.submit`/`retry`, the `JobBackend` protocol)
- Modify: `src/splitsmith/db/models.py` (`ComputeJobRow`)
- Modify: `src/splitsmith/db/job_backend.py` (`submit`, `retry`, row to Job, new queries)
- Modify: `src/splitsmith/queue.py` (the defer args and the worker's ContextVar set, near `current_match_id.set` at ~555)
- Create: `alembic/versions/<rev>_export_id_and_parts_on_compute_jobs.py` (down_revision `e7a1c3d9f5b2`)
- Test: `tests/test_export_identity.py`

**Interfaces:**
- Produces: `current_export_id: ContextVar[str | None]` in `ui/context.py`.
- Produces: `contextlib.contextmanager export_scope(export_id: str) -> Iterator[None]` in `ui/context.py`; it sets and resets the var.
- Produces: `Job.export_id: str | None = None`, and `Job.parts: list[JobPart] | None = None` (the field is declared here; `JobPart` is Task 2's).
- Produces: on `JobBackend`: `async def list_export(self, export_id: str) -> list[Job]` and `async def active_export_ids(self) -> list[str]`. Active follows Global Constraints: any job pending or running, except a `compare-grid-stage` job whose `compare-grid` sibling is terminal.
- Produces: columns `compute_jobs.export_id` (String, nullable, indexed) and `compute_jobs.parts` (JSON/JSONB, nullable).

- [ ] **Step 1: Write the failing tests** in `tests/test_export_identity.py`:
  - `test_jobs_submitted_in_a_scope_carry_its_id`: for the local `JobRegistry` and for `PostgresJobBackend` over the sqlite harness (`tests/test_hosted_raw_upload.hosted_db`), two submits inside `export_scope("e1")` and one outside. `list_export("e1")` returns exactly the two, and the third has `export_id is None`.
  - `test_a_chained_submit_inherits_the_export`: a body registered for kind `"x"` submits kind `"y"`. Run it under `export_scope("e1")` through the local registry, and on hosted through `tests/test_hosted_compare_grid.inline_deferrer` (the worker path). Assert `y.export_id == "e1"`.
  - `test_retry_keeps_the_export`: a failed job in `"e1"`, retried; the new job's `export_id == "e1"`.
  - `test_active_export_ids_follow_the_parts`: `"e1"` has one running job and `"e2"` only finished ones, so the result is `["e1"]`. A `compare-grid-stage` left pending beside a failed `compare-grid` does not make its export active.
- [ ] **Step 2: Run them.** `uv run pytest -n0 tests/test_export_identity.py`. Expected: FAIL, `export_scope` does not exist.
- [ ] **Step 3: Implement.**
  - Both backends read `current_export_id.get()` at submit and store it.
  - Hosted adds `export_id` to the deferred args, and the worker sets the var next to `current_match_id`.
  - Local `submit` already runs the body in `copy_context()`, so chained submits inherit with no extra code.
  - `retry` sets the var from the row (hosted, beside `current_match_id.set(row_match_id)`) or from the stored match context tuple (local; extend it with the export id).
  - The migration adds both columns.
- [ ] **Step 4: Run them.** Expected: PASS. Then `uv run pytest -q tests/test_jobs.py tests/test_jobs_poll_payload.py tests/test_hosted_compare_grid.py tests/test_hosted_grid_stage_jobs.py`: PASS.
- [ ] **Step 5: Commit.** `feat(jobs): tag every job an export queues with its export`

### Task 2: Parts a job publishes

**Files:**
- Modify: `src/splitsmith/ui/jobs.py` (`JobPart`, `JobHandle.set_parts`/`update_part`)
- Modify: `src/splitsmith/db/job_backend.py` (persist `parts`)
- Test: `tests/test_job_parts.py`

**Interfaces:**
- Consumes: `Job.parts` (Task 1).
- Produces:
  ```python
  class JobPart(BaseModel):
      key: str                      # stable within the job: "plan", "stage-3", "stitch", "upload"
      label: str                    # UI copy: "Stage 3 (Speed)"
      kind: str                     # estimate class, see Task 5's table
      profile: str | None = None    # "1080-basic", "2160-dressed"; None when not a grid
      status: Literal["waiting", "running", "done", "failed", "skipped"] = "waiting"
      progress: float | None = None
      footage_s: float | None = None
      started_at: datetime | None = None
      finished_at: datetime | None = None
  ```
- Produces: `JobHandle.set_parts(parts: list[JobPart]) -> None` and `JobHandle.update_part(key: str, *, status: str | None = None, progress: float | None = None) -> None`.
  - Moving to `running` stamps `started_at`; `done`, `failed` and `skipped` stamp `finished_at`.
  - A progress-only update persists at most once per `PART_WRITE_INTERVAL_S = 3.0` per job, and a status change always persists.
  - An unknown key raises `KeyError`.

- [ ] **Step 1: Write the failing tests:** `test_parts_round_trip_through_both_backends`, `test_status_changes_stamp_times`, `test_progress_writes_are_throttled_but_status_is_not` (count `_patch` calls with a fake clock), `test_unknown_part_key_raises`.
- [ ] **Step 2: Run them.** `uv run pytest -n0 tests/test_job_parts.py`. Expected: FAIL on import.
- [ ] **Step 3: Implement** in `jobs.py`. The handle keeps the parts list in memory and writes the whole list through `_registry._patch(job_id, parts=...)`.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(jobs): let a job publish its parts and their progress`

### Task 3: Progress from inside an encode

**Files:**
- Create: `src/splitsmith/ffmpeg_progress.py`
- Modify: `src/splitsmith/compare/mp4_grid.py`:
  - `GridRenderStep` gains `fraction: float | None = None`;
  - `_GridEncoder` stage-item encodes (~3225 and ~3252) and the stitch (~4546) run under the watcher when a progress hook is set.
- Test: `tests/test_ffmpeg_progress.py`, plus additions to `tests/test_compare_mp4_grid_render.py`

**Interfaces:**
- Produces: `@contextmanager def ffmpeg_progress(duration_s: float, on_fraction: Callable[[float], None], *, interval_s: float = 2.0) -> Iterator[tuple[str, ...]]`.
  - It yields the extra argv `("-progress", <file in a temp dir>, "-nostats")`.
  - A daemon thread reads the file's last `out_time_us=` every `interval_s` and calls `on_fraction(min(1.0, out_time_us / 1e6 / duration_s))`.
  - On exit it stops the thread, deletes the file, and calls `on_fraction(1.0)` only if the block exited cleanly.
- Produces: the grid's `progress` hook also receives `GridRenderStep(status="encoding", fraction=f)` while an item encodes, and `status="stitching", fraction=f` during the stitch.

- [ ] **Step 1: Write the failing tests:**
  - `test_fractions_follow_out_time`: a fake writer appends `out_time_us=` lines to the yielded path, and the callback sees rising fractions capped at 1.0.
  - `test_no_final_one_on_error`.
  - `test_a_stage_encode_reports_fractions` (integration, real ffmpeg on a `tests/synthetic_media.py` clip): the hook sees at least one `0 < fraction < 1` for a stage.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** The runner stays `subprocess.run`-shaped, which is why progress goes through a file and not stderr.
- [ ] **Step 4: Run them.** Use `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_ffmpeg_progress.py tests/test_compare_mp4_grid_render.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(grid): report progress from inside a stage encode and the stitch`

### Task 4: Every export kind publishes its parts

**Files:**
- Modify: `src/splitsmith/ui/server.py`:
  - `_run_compare_grid` (~2294), `_run_compare_grid_stage` / `_render_one_grid_stage` (~2147-2290) and `_compare_grid_progress` (~1901);
  - `_run_match_export` (~4129, its per-stage loop and `_render_step`);
  - `_run_export_for_stage` (registered at ~4763).
- Modify: `src/splitsmith/ui/highlights_api.py` (`run_highlight_reel`), `src/splitsmith/youtube/...` upload body (`run_youtube_upload`)
- Test: `tests/test_export_parts.py`

**Interfaces:**
- Consumes: `JobHandle.set_parts` / `update_part` (Task 2); `GridRenderStep.fraction` (Task 3).
- Produces these part lists. `profile` is `f"{canvas_height}-{'dressed' if overlay else 'basic'}"` for grid kinds.

  | Job kind | Parts (key: kind) |
  |---|---|
  | `compare-grid`, hosted with stage jobs | `plan: load`, `stages: wait` (label "Stage renders"), `stitch: stitch` (footage_s = sum of stage durations) |
  | `compare-grid`, no stage jobs (desktop, or a one-stage grid) | `plan: load`, one `stage-N: grid_stage` per planned stage (footage_s = `duration_seconds * len(tiles)`), `stitch: stitch` |
  | `compare-grid-stage` | `render: grid_stage` (footage_s as above) |
  | `match_export` | `load: load`, one `stage-N: stage_export` per stage, `compose: compose`, plus `upload: upload` when hosted |
  | `export` (one stage) | `export: stage_export` |
  | `highlight_reel` | `load: load`, `render: reel`, plus `upload: upload` when hosted |
  | `youtube_upload` | `upload: youtube` |

- [ ] **Step 1: Write the failing tests:**
  - for each kind, run the body with its renderer faked (as `tests/test_hosted_grid_stage_jobs.py` fakes it) and assert the final `parts` keys, kinds and statuses: all `done`, and `skipped` for a stage whose trim was already present;
  - `test_a_failed_stage_part_is_failed_and_the_grid_still_succeeds`.
- [ ] **Step 2: Run them.** Expected: FAIL (parts None).
- [ ] **Step 3: Implement.** `set_parts` goes at the top of each body. Map existing `handle.update` sites onto `update_part`, and keep `handle.update` for the job's overall progress and message: the drawer still reads them.
- [ ] **Step 4: Run them.** Then `uv run pytest -q tests/test_compare_grid_endpoint.py tests/test_hosted_compare_grid.py tests/test_hosted_grid_stage_jobs.py tests/test_match_export*.py`: PASS.
- [ ] **Step 5: Commit.** `feat(export): every export job publishes its parts`

### Task 5: Estimates

**Files:**
- Create: `src/splitsmith/export_estimates.py`
- Test: `tests/test_export_estimates.py`

**Interfaces:**
- Consumes: `Job`, `JobPart` (Tasks 1-2).
- Produces:
  ```python
  BUILTIN_PART_SECONDS: dict[tuple[str, str | None], float]  # (part kind, profile) -> seconds
  WORKER_BOOT_S: float = 60.0
  class PartEstimate(BaseModel): job_id: str; key: str; remaining_s: float; overrun: bool
  class ExportEstimate(BaseModel):
      time_left_s: float; finish_at: datetime; approximate: bool; parts: list[PartEstimate]
  def history_rates(finished: list[Job]) -> dict[tuple[str, str | None], float]  # seconds per footage-second, median
  def estimate_export(jobs: list[Job], history: dict[tuple[str, str | None], float], *, now: datetime, hosted: bool) -> ExportEstimate
  ```
- Built-in values:

  | Part | Seconds |
  |---|---|
  | `("grid_stage","1080-basic")` | 300 |
  | `("grid_stage","1080-dressed")` | 480 |
  | `("grid_stage","2160-basic")` | 420 |
  | `("grid_stage","2160-dressed")` | 700 |
  | `("stitch","1080-basic")`, `("stitch","1080-dressed")` | 100 |
  | `("stitch","2160-basic")`, `("stitch","2160-dressed")` | 270 |
  | `("load",None)` | 20 |
  | `("stage_export",None)` | 60 |
  | `("compose",None)` | 120 |
  | `("upload",None)` | 30 |
  | `("reel",None)` | 180 |
  | `("youtube",None)` | 60 |
  | anything else | 60 |

- A part's planned seconds: the history rate times `footage_s` when both exist, else the built-in value. `approximate` is True when any part with work left used a built-in value.
- A part's remaining seconds:
  - `done` or `skipped`: 0.
  - `running` with `progress >= 0.05`: `elapsed * (1 - p) / p`.
  - `running` otherwise: `max(0, planned - elapsed)`, with `overrun = elapsed > planned`.
  - `waiting`: `planned`.
  - Add `WORKER_BOOT_S` when `hosted` and the part's job is still `pending`.
- Time left: the lead job is the earliest `compare-grid`, `match_export` or `highlight_reel`.
  - Without a lead: the max over jobs of each job's parts summed.
  - With a lead: its parts summed, where a `wait` part counts as the max over the other jobs' remaining.
  - A job after the lead (the YouTube upload) adds its sum.

- [ ] **Step 1: Write the failing tests:**
  - `test_grid_time_left_is_the_longest_stage_plus_the_stitch`;
  - `test_no_history_is_approximate_and_uses_builtins`;
  - `test_history_scales_by_footage`;
  - `test_running_part_uses_its_rate`;
  - `test_a_part_past_its_estimate_is_overrun_not_negative`;
  - `test_pending_hosted_job_adds_worker_boot`.
- [ ] **Step 2: Run them.** Expected: FAIL on import.
- [ ] **Step 3: Implement** as pure functions with no I/O.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(export): estimate an export's time left from its parts`

### Task 6: The limit

**Files:**
- Modify: `src/splitsmith/access.py` (config and resolver)
- Modify: `src/splitsmith/db/models.py` (`User.concurrent_exports`)
- Create: `alembic/versions/<rev>_users_concurrent_exports.py` (down_revision: Task 1's)
- Modify: `src/splitsmith/db/access_requests.py` (the accounts store: `set_concurrent_exports`; `AccountView` gains the field)
- Modify: `src/splitsmith/ui/admin_access_api.py` (`PATCH /users/{id}` body accepts `concurrent_exports: int | None`)
- Create: `src/splitsmith/ui/export_slot.py`
- Modify: the export routes:
  - `ui/exports_api.py`: `export_stage` ~533, after its `find_active` join; `export_match` ~714, likewise;
  - `ui/highlights_api.py`: `_submit` ~160;
  - `ui/match_api.py`: compare-export ~1895, around the existing `hold_wakes()` block.
- Create: route `POST /api/shooters/{slug}/exports/trims` in `ui/exports_api.py`. Body `{"stage_numbers": list[int]}`; it queues one `export` job per stage with the trim-only flags `src/splitsmith/ui_static/src/pages/Export.tsx` `submitTrims` sends today, inside one slot.
- Test: `tests/test_export_limit.py`

**Interfaces:**
- Produces:
  - `AccessConfig.concurrent_exports: dict[str, int] = {}`
  - `DEFAULT_CONCURRENT_EXPORTS = 1`
  - `def export_limit(tier: str | None, override: int | None) -> int`: the override when set, else the tier's number, else the default.
- Produces: `@asynccontextmanager async def export_slot(request: Request, *, export_id: str | None = None) -> AsyncIterator[str]` in `ui/export_slot.py`.
  - Local mode: yields a new id (or the given one) under `export_scope` and `hold_wakes`, with no check.
  - Hosted:
    1. Reads the user's tier and override in one query.
    2. Serializes per user: Postgres `pg_advisory_lock(hashtext(user_id))` on a dedicated connection held for the block; in-process `asyncio.Lock` per user on sqlite.
    3. Counts `state.jobs.active_export_ids()` excluding the given `export_id`.
    4. Over the limit, raises `HTTPException(409, {"code": "export_limit", "running": ExportSummary})`, using the oldest active export.
    5. Otherwise yields under `export_scope` and `hold_wakes`, and unlocks in `finally`.
- Produces: `ExportSummary(BaseModel): export_id: str; kind: str; label: str; match_id: str | None` (label as `lib/jobLabels.jobTarget` would show it: kind label plus target).

- [ ] **Step 1: Write the failing tests:**
  - `test_a_second_export_is_refused_with_the_running_one`;
  - `test_the_override_raises_the_limit` (override 2, so two are admitted and the third is refused);
  - `test_a_tier_number_is_used_without_an_override`;
  - `test_a_finished_export_frees_the_slot`;
  - `test_joining_a_running_stage_export_is_not_a_new_export`;
  - `test_trims_only_is_one_export` (three stages, one `export_id`, admitted at limit 1);
  - `test_two_concurrent_starts_admit_one` (two `asyncio.gather`ed slot entries for one user on the sqlite harness);
  - `test_local_mode_has_no_limit`;
  - `test_admin_sets_the_override`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement**, and add both migrations to the alembic chain test if one exists (`tests/test_migrations*.py`).
- [ ] **Step 4: Run them.** Then `uv run pytest -q tests/test_admin_access*.py tests/test_access*.py tests/test_compare_grid_endpoint.py tests/test_hosted_grid_stage_jobs.py`: PASS.
- [ ] **Step 5: Commit.** `feat(export): limit how many exports a hosted account runs at once`

### Task 7: The export routes

**Files:**
- Create: `src/splitsmith/ui/exports_progress_api.py` (router mounted where `me_api` is)
- Test: `tests/test_exports_progress_api.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 5 and 6.
- Produces these routes:
  - `GET /api/me/exports/active -> ActiveExports{limit: int | None, exports: list[ExportView]}`. `limit` is None on desktop. It covers every match of the user. The history for estimates comes from one query for the user's last 200 finished export jobs, never per row.
  - `GET /api/me/exports/{export_id} -> ExportView`, 404 when unknown or another user's.
  - `POST /api/me/exports/{export_id}/cancel -> ExportView`: cancels every pending or running job of it.
  - `POST /api/me/exports/{export_id}/retry` with body `{"job_id": str}` `-> ExportView`, under `export_slot(request, export_id=export_id)`:
    - a failed `compare-grid-stage`, or a failed `compare-grid`, re-submits a `compare-grid` with the original args and `stage_jobs={}`;
    - any other failed job goes through `state.jobs.retry(job_id)`;
    - 409 when the job is not failed.
- Produces:
  ```python
  class ExportRow(BaseModel):
      job_id: str; key: str; label: str; status: str; progress: float | None
      elapsed_s: float | None; remaining_s: float; overrun: bool; retryable: bool
  class ExportView(BaseModel):
      export_id: str; kind: str; label: str; match_id: str | None; status: str
      started_at: datetime; time_left_s: float | None; finish_at: datetime | None
      approximate: bool; rows: list[ExportRow]
  ```
- Rows: a job's parts, or the job itself as one row when it has none. A grid's `wait` part is left out (its stage jobs are the rows).
- Status rule: running while any job is pending or running (Task 1's active rule); otherwise the lead's status; with no lead, failed if any failed, else cancelled if any cancelled, else succeeded.

- [ ] **Step 1: Write the failing tests:**
  - `test_active_shows_a_grid_with_its_stage_rows`;
  - `test_active_lists_exports_across_matches`;
  - `test_cancel_cancels_every_part`;
  - `test_retry_a_failed_stage_requeues_the_grid_in_the_same_export`;
  - `test_retry_refuses_a_job_that_did_not_fail`;
  - `test_another_users_export_is_404`;
  - `test_active_is_one_history_query` (count statements with SQLAlchemy events).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Then run `uv run python scripts/export_openapi.py`, and `pnpm gen:api` in `src/splitsmith/ui_static`.
- [ ] **Step 4: Run them.** Then `uv run pytest -q tests/test_openapi_export.py`: PASS.
- [ ] **Step 5: Commit.** `feat(export): routes for an export's progress, cancel and retry`

### Task 8: The panel, the button and the drawer

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (aliases `ExportView`, `ActiveExports` into `Schema[...]`; wrappers `activeExports`, `cancelExport`, `retryExportPart`, `exportTrims`)
- Create: `src/splitsmith/ui_static/src/lib/useActiveExports.ts`. It polls `/api/me/exports/active` every 3 s while the page is visible and any export is active, every 30 s otherwise, and once on focus.
- Create: `src/splitsmith/ui_static/src/components/export/ExportProgress.tsx` (+ `.test.tsx`)
- Modify: `src/splitsmith/ui_static/src/pages/Export.tsx`:
  - the panel replaces the `busy && job?.message` block (~1223);
  - `submitTrims` (~615) calls `exportTrims` once;
  - the Export button is disabled when `limit != null && exports.length >= limit`, with the note "An export is running" and a link that scrolls to the panel (same match) or navigates to `/m/<match_id>/export` (another match);
  - the 409 `export_limit` detail shows the same note.
- Modify: `src/splitsmith/ui_static/src/components/Jobs.tsx` and `src/splitsmith/ui_static/src/lib/jobLabels.ts`: jobs sharing an `export_id` fold into one row, the lead's (else the first's), whose progress is the mean of its jobs' progress.
- Modify: `src/splitsmith/ui_static/src/pages/AdminAccess.tsx`: a number field "Exports at once", blank for the tier default.
- Modify: `src/splitsmith/data/whats_new.json`: one entry, id `export-progress`.

**Interfaces:**
- Consumes: Task 7's routes.
- The panel shows:
  - header: kind label plus target, then "about 9 min left · done by 14:32" (drop "about" when `approximate` is false; "taking longer than usual" when every remaining row is overrun);
  - rows: label, status word, a `ProgressStrip`-style bar, elapsed or taken time, and a Retry button on `retryable` rows;
  - Cancel through `ConfirmDialog`.
  - It stays within the primitives and visual budget in `.claude/rules/ui-budget.md`, and stacks on phones.

- [ ] **Step 1: Write the failing tests** (vitest):
  - `ExportProgress.test.tsx`: renders rows and time left; "taking longer than usual" when overrun; Retry calls `retryExportPart`; Cancel confirms then calls `cancelExport`.
  - The Export page test: the button is disabled with the note and link when the limit is reached, enabled on desktop (`limit: null`).
  - The Jobs test: three jobs with one `export_id` render one row.
- [ ] **Step 2: Run them.** `pnpm test -- ExportProgress Export Jobs`. Expected: FAIL.
- [ ] **Step 3: Implement.** Write the What's new entry with the `whats-new` skill.
- [ ] **Step 4: Check.** Run `pnpm test`, `pnpm typecheck` and `uv run pytest -n0 tests/test_whats_new.py`: PASS. Show the rendered page per CLAUDE.md (seed the demo match with `--media`, start an export, screenshot the panel at desktop and phone widths).
- [ ] **Step 5: Commit.** `feat(export): show an export's progress, parts and time left on the Export page`

### Task 9: Guidance

**Files:**
- Modify: `.claude/rules/hosted.md` (the limit and `export_slot`), `.claude/rules/export-ui.md` (the panel, `useActiveExports`, the folding rule)

- [ ] **Step 1:** Add one bullet each.
  - `hosted.md`: every export route starts its jobs inside `export_slot`, and a new export kind joins Task 7's lead list and Task 4's parts table.
  - `export-ui.md`: the panel reads only `/api/me/exports/active`.
- [ ] **Step 2:** Run `uv run python scripts/check_claude_md.py`. Expected: no output.
- [ ] **Step 3: Commit.** `docs(rules): exports run inside a slot and publish their parts`
