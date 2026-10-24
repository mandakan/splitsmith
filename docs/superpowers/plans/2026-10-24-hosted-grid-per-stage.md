# Hosted compare grid per stage: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hosted whole-match compare grid renders as one job per stage plus a stitch, with memory bounded by one stage, and a basic 1080p HFO grid ready in 10 to 15 minutes.

**Architecture:**
- **The local renderer gains three seams, all off by default**, so the local and CLI paths keep their argv and cache keys:
  - stable tile identities in segment cache keys;
  - a per-stage tile acquire hook;
  - a single-stage mode.
- **Hosted, a `compare-grid-stage` job per stage** renders its stage into an R2-backed segment cache.
- **The `compare-grid` job** queues those stage jobs, waits for them, then runs the normal render, in which every stage segment is a cache hit.

**Tech Stack:** Python 3.11+, ffmpeg, procrastinate jobs, R2 through `splitsmith.storage`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-24-hosted-grid-per-stage-design.md`

## Global Constraints

- **No new dependencies.**
- **The local render path is unchanged.** With the new parameters at their defaults, every ffmpeg argv and every `SegmentCache.key` is byte-identical to main, so `segment_cache.KEY_VERSION` stays 5.
- **No request runs ffmpeg or pulls media** (`.claude/rules/media.md`).
- **A presigned URL never appears in a cache key, a log line or an error message** (`proxy.redact_urls`).
- **A URL read is trusted only when ffmpeg's stderr is empty.**
- **Fan-out cap:** `SPLITSMITH_WORKER_MAX_EXECUTIONS: "13"` in both `deploy/cloudrun/*-serve.env.yaml`.
- **Prose** (docs, comments, What's new): ASCII only, no dash punctuation.
- **Run tests through `~/.claude-tmp/with-project-ffmpeg.sh`.** Integration tests are marked `@pytest.mark.integration` and build media with `tests/synthetic_media.py`.

## Review Focus

1. **A shooter with no trim on a stage (a filler tile).** The stage job renders the filler, and its key carries no tile identity for the missing tile. Test in Task 3.
2. **An inset camera.** The inset clip is a tile input too: acquire pulls it, and it has an identity. Test in Task 5.
3. **A re-export after an audit or beep change.** The changed export object has a new version, so the stage's key changes and the stage renders again; the other stages stay hits. Test in Task 5.
4. **The parent is killed mid-wait** (OOM or timeout) while its stage jobs finish. A retried grid reuses every segment those jobs stored. Test in Task 6.
5. **Two grid requests for the same match at once**, the same canvas or different ones. Both complete. Identical keys may encode twice, but a commit is atomic, both locally and in R2. Test in Task 4.

---

### Task 0: Measure one stage on staging (no code)

**Files:** none. Results go in a comment on #1691.

- [ ] **Step 1: Render stage 1 of the A9 match as a basic 1080p grid on staging.** The A9 match is `hfo-masters-2026-bench-a9-full-c-e23eae5d60`; its exports exist.

```bash
cd ~/.claude-tmp/bench && C=$(cat bench.cookie)
curl -s -b "splitsmith_session=$C" -H 'content-type: application/json' \
  -H "X-Splitsmith-Match: hfo-masters-2026-bench-a9-full-c-e23eae5d60" \
  https://my.staging.splitsmith.app/api/match/compare-export \
  -d '{"stage_numbers":[1],"audio_from":"<first shooter slug from run-A9.json>","canvas_width":1920,"canvas_height":1080,"output_name":"m-1080-basic"}'
```

Expected: a job id. Check how the bench authenticates (`scripts/match_bench.py` on `feat/match-bench`) and use the same header and cookie names.

- [ ] **Step 2: Render the same stage dressed at 1080p and basic at 4K.**
  - Dressed: the same body plus `"overlay":true,"summary_hold_seconds":3,"stage_titles":"slate","transition_kind":"fade"`. A transition needs two stages, so dressed uses stages `[1,2]`; divide its time by two.
  - Basic at 4K: `canvas_width 3840`, `canvas_height 2160`.

- [ ] **Step 3: Record each run from `compute_jobs`:**
  - `timings.phases`;
  - `timings.worker.peak_rss_mb` and `peak_child_rss_mb`;
  - the output's size in R2, under `matches/<id>/exports/`.

  Post them on #1691.

  **Gate:** if a basic 1080p stage takes more than 240 s of render, stop and bring the numbers back before Task 1. The 10 to 15 minute target assumes one round of stage jobs.

  **4K note:** the stitch in Task 6 pulls every stage segment onto the parent's memory disk beside the output. If 12 times the 4K stage segment size is over 2.5 GB, say so on #1691. 4K then needs the stitch to read segments over presigned URLs, as a follow-up after this plan; 1080p is the target here.

---

### Task 1: Stable tile identities in grid cache keys

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py`: `render_grid_mp4` signature; `_GridEncoder.__init__` and `encode`
- Test: `tests/test_compare_mp4_grid_render.py`

**Interfaces:**
- Produces: `render_grid_mp4(..., tile_identities: Mapping[str, str] | None = None)`.
  - Each key is an argv token, the string of a tile input path (a stage trim or an inset clip).
  - Each value is that file's stable identity, for example `"storage:<key>:<size>:<last_modified iso>"`.
  - `_GridEncoder(cache, work, tile_identities=None)` merges these into `virtual_inputs` for every encode.

- [ ] **Step 1: Write the failing tests**

```python
def test_tile_identities_make_keys_independent_of_the_tile_files(tmp_path):
    # Two renders whose tiles are different files on different paths, with
    # the same tile_identities, produce the same SegmentCache keys for every
    # stage segment (the second render is all hits: its runner is never called
    # for a stage encode).
    ...
    assert second_runner_stage_calls == []

def test_without_tile_identities_keys_are_unchanged(tmp_path):
    # The keys main computes for the existing fixture render: recorded with
    # tile_identities=None, equal to the keys from a render on main.
    assert keys_with_none == keys_recorded_on_main
```

Record `keys_recorded_on_main` by running the existing fixture render on main before the change. Use the fakes `tests/test_compare_mp4_grid_render.py` already has.

- [ ] **Step 2: Run them.**
  - Command: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_compare_mp4_grid_render.py -k "tile_identities" -v`
  - Expected: the first test fails, because `tile_identities` is an unexpected keyword. The second passes.

- [ ] **Step 3: Implement.** Thread `tile_identities` from `render_grid_mp4` to `_GridEncoder`.
  - In `encode`, pass `virtual_inputs={**(tile_identities or {}), **(virtual_inputs or {})}`.
  - Pass `None` when both are empty, so the key is unchanged when nothing is given.

- [ ] **Step 4: Run the whole grid suite.**
  - Command: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest tests/test_compare_mp4_grid_*.py tests/test_compare_cli_mp4.py -q`
  - Expected: all pass.

- [ ] **Step 5: Commit** `feat(grid): key tile inputs by a stable identity when the caller has one`

---

### Task 2: Acquire a stage's tiles only when an encode needs them

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py`
- Test: `tests/test_compare_mp4_grid_render.py`

**Interfaces:**
- Produces: `class TileAccess(Protocol): def acquire(self, stage_number: int) -> contextlib.AbstractContextManager[None]: ...` in `mp4_grid.py`, and `render_grid_mp4(..., tile_access: TileAccess | None = None)`.
- Every place that reads a stage's tile files runs inside `tile_access.acquire(n)`:
  - a stage or edge encode, only on a cache miss: `_GridEncoder.encode(..., tiles_for: int | None = None)` enters the acquire around the ffmpeg run, never on a hit;
  - a card backdrop grab (`_card_assets`);
  - the match summary freezes (`extract_match_summary_freezes`, one stage at a time);
  - the free cell still;
  - the summary hold freeze frames.
- `None` means the tiles are on disk, which is today's behaviour.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_fully_cached_render_acquires_no_tiles(tmp_path): ...
    assert access.acquired == []

def test_a_cold_render_holds_at_most_one_stage_at_a_time(tmp_path): ...
    assert access.max_held == 1
    assert sorted(set(access.acquired)) == [1, 2, 3]

def test_a_cold_dressed_render_acquires_around_cards_and_freezes(tmp_path):
    # slate titles, summary hold, match summary, one fade: every tile read
    # happens inside an acquire of its own stage
    assert access.reads_outside_acquire == []
```

The fake `TileAccess` records `acquired`, `max_held` and which stage is held. The fake runner records each tile path an ffmpeg call reads while nothing holds that path's stage, into `reads_outside_acquire`.

- [ ] **Step 2: Run them.**
  - Command: `... pytest -n0 tests/test_compare_mp4_grid_render.py -k acquire -v`
  - Expected: FAIL.

- [ ] **Step 3: Implement as in Interfaces.** The plans already know each tile's stage: `GridStagePlan.stage_number`.

- [ ] **Step 4: Run the grid suite.** Same command as Task 1, Step 4. Expected: all pass.

- [ ] **Step 5: Commit** `feat(grid): read a stage's tiles only inside its acquire`

---

### Task 3: Render one stage of a grid

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py`
- Test: `tests/test_compare_mp4_grid_render.py`, `tests/test_compare_mp4_grid_transitions_integration.py`

**Interfaces:**
- Produces: `render_grid_mp4(..., only_stage: int | None = None)`.
- With `only_stage=n`, it still plans the whole spine with the same probes, capability checks and degradations. Then:
  - it encodes only stage `n`'s segment, and the head and tail edge segments of any boundary next to stage `n`;
  - it encodes no cards, no boundary segments, no match summary and no stitch;
  - it returns `GridRenderResult` with `output_path=output_path` (the file is not written) and `stages` holding one `StageOutcome` for `n`;
  - with `segment_cache=None` it raises `ValueError`, because a stage render with nowhere to keep its segment is a bug.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("dressed", [False, True])
def test_stage_renders_then_a_full_render_is_all_hits(tmp_path, dressed):
    # dressed: slate titles, summary hold, a fade between every stage
    # 1. only_stage=n for every stage into one cache
    # 2. a full render into the same cache with runner/boundary_runner that
    #    record calls: no stage or edge encode runs, only cards, boundaries
    #    and the stitch
    # 3. its concat list equals the concat list of a one-process render of
    #    the same request into a fresh cache
    assert stage_and_edge_encodes == []
    assert concat_list_split == concat_list_one_process

def test_a_stage_with_a_filler_tile_renders_alone(tmp_path):
    # one shooter has no trim on stage 2 (filler); only_stage=2 succeeds and
    # the full render reuses it
    ...

def test_only_stage_without_a_cache_is_refused(tmp_path):
    with pytest.raises(ValueError):
        render_grid_mp4(..., only_stage=1, segment_cache=None)
```

Add one integration test to `tests/test_compare_mp4_grid_transitions_integration.py`. It runs real ffmpeg on synthetic media, with a fade and two stages:
- the split render and the one-process render produce outputs with identical frame digests (`tests.test_proxy._frame_digest`);
- they produce identical audio streams.

- [ ] **Step 2: Run them.** Expected: FAIL, because `only_stage` is unknown.

- [ ] **Step 3: Implement.** In the items loop, an item that is not stage `only_stage` is skipped:
  - a boundary next to it still decides its fit and encodes the edge on stage `only_stage`'s side;
  - the boundary segment and the stitch are skipped;
  - progress reports one stage.

- [ ] **Step 4: Run** the grid suite, plus `-m integration tests/test_compare_mp4_grid_transitions_integration.py`. Expected: all pass.

- [ ] **Step 5: Commit** `feat(grid): render one stage's segments into the cache`

---

### Task 4: A segment cache backed by R2

**Files:**
- Create: `src/splitsmith/stored_segment_cache.py`
- Test: `tests/test_stored_segment_cache.py`

**Interfaces:**
- Produces: `class StoredSegmentCache(SegmentCache)`.
  - Constructor: `StoredSegmentCache(root: Path, max_bytes: int, *, storage: Storage, prefix: str)`.
  - `prefix` is `f"matches/{match_id}/render-segments"`.
  - `lookup(key, suffix)` returns the local file when present. Otherwise it pulls `f"{prefix}/{key}{suffix}"` into `path_for(key)` through a partial and rename. It returns `None` when the object is missing or the pull fails, and logs the failure.
  - `commit(partial, key, suffix)` commits locally, then pushes the object. A failed push is logged and leaves the local commit, so the render still has its segment.
  - `forget_local(key, suffix)` deletes the local file only. A stage job calls it after its push, so a drain does not keep segments in memory.

- [ ] **Step 1: Write the failing tests.** They run against the in-memory storage that `tests/test_hosted_web_only_trims.py` uses.
  - `test_a_commit_is_found_by_another_root`: a commit from root A is found by a lookup from an empty root B with the same prefix.
  - `test_a_missing_object_is_a_miss`
  - `test_a_failed_pull_is_a_miss_and_leaves_no_partial`
  - `test_two_commits_of_one_key_both_succeed`: Review Focus 5.
  - `test_forget_local_keeps_the_stored_copy`

- [ ] **Step 2: Run.** Expected: FAIL with an import error.

- [ ] **Step 3: Implement.**
  - Pull with `storage.fetch_to_path` or the call `export_storage.pull_export_file` uses.
  - Push with `storage.write_file` or its equivalent.
  - Check `storage.py` for the exact names.

- [ ] **Step 4: Run.** Expected: PASS.

- [ ] **Step 5: Confirm the segments go when the match goes.** Find the hosted match delete; it deletes `matches/<id>/` objects. Add `test_deleting_a_match_deletes_its_render_segments` to that path's test file.

- [ ] **Step 6: Commit** `feat(grid): keep hosted grid segments in R2`

---

### Task 5: Hosted bundles, tile identities and tile access

**Files:**
- Modify: `src/splitsmith/ui/server.py`: `_load_compare_bundles`, and a new `HostedGridTiles`
- Modify: `src/splitsmith/compare/project_loader.py` only if a hook is missing
- Test: `tests/test_hosted_grid_tiles.py`

**Interfaces:**
- Consumes: Tasks 1, 2 and 4.
- Produces:
  - **`_load_compare_bundles(..., lazy_tiles: bool = False)`.** With `True`, nothing is pulled: `ensure_trim` is a no-op, and `probe` is `_probe_stored_trim`, which runs ffprobe on a presigned URL of the trim's object with the URL redacted from errors. Bundles carry local paths that do not exist yet. `_cut_stage_from_source` is deferred to acquire.
  - **`class HostedGridTiles`** (in `server.py`, beside `_load_compare_bundles`):
    - Constructor: `HostedGridTiles(bundles, state, cut_dir: Path)`.
    - Properties: `identities: dict[str, str]` and `access: TileAccess`.
    - `identities` maps each tile path, inset clips included, to `storage:<object key>:<size>:<last_modified>` from one storage listing.
    - A tile cut from the source maps to `source-cut:<source key>:<size>:<last_modified>:<cut name>`. `_source_cut_name` already carries stage and video; add the beep time if it does not.
    - `access.acquire(n)` pulls stage `n`'s tiles (exports, inset clips, cuts from the source) and deletes them on exit.

- [ ] **Step 1: Write the failing tests**

```python
def test_lazy_bundles_pull_nothing(tmp_path, ...):           # storage.fetched == []
def test_identities_are_the_same_from_two_workers(...):      # two roots, equal dicts, no "http" in any value
def test_identity_changes_when_the_export_changes(...):      # Review Focus 3
def test_acquire_pulls_one_stage_and_releases_it(...):       # files exist inside, gone after
def test_an_inset_clip_is_acquired_and_identified(...):      # Review Focus 2
def test_a_tile_cut_from_the_source_is_identified_without_cutting(...)
```

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement** as in Interfaces.

- [ ] **Step 4: Run** the new file, plus `tests/test_compare_grid_endpoint.py` and `tests/test_compare_audit_trim_integration.py`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(grid): hosted bundles that pull a stage's tiles only when it renders`

---

### Task 6: Stage jobs, the waiting parent, cancel and fallback

**Files:**
- Modify: `src/splitsmith/ui/server.py`: `_run_compare_grid`, a new `_run_compare_grid_stage`, and registration next to `bodies.register("compare-grid", ...)`
- Modify: `src/splitsmith/ui/job_journal.py`: the `"compare-grid-stage": CompareGridRequest` model
- Modify: `src/splitsmith/ui/auto_sync.py`: add the kind wherever `"compare-grid"` is listed
- Modify: `src/splitsmith/ui_static/src/lib/jobLabels.ts`: `"compare-grid-stage": "Grid stage"`
- Test: `tests/test_hosted_grid_stage_jobs.py`

**Interfaces:**
- Consumes: Tasks 3 to 5.
- Produces:
  - **`_run_compare_grid_stage(handle, req: CompareGridRequest, match_root: str, stage_number: int, *, state)`**:
    - loads lazy bundles and `HostedGridTiles`;
    - renders with `only_stage=stage_number`, `segment_cache=StoredSegmentCache(<job temp dir>, ..., prefix=...)`, `tile_identities` and `tile_access`;
    - calls `forget_local` for each committed key.
  - **`_run_compare_grid`, hosted** (when `state.storage` is set):
    - plans with lazy bundles;
    - submits one `compare-grid-stage` job per planned stage, with `args={"req": req, "match_root": match_root, "stage_number": n}`;
    - waits, polling `state.jobs.get` every `GRID_STAGE_POLL_S = 5.0` and updating progress as `Rendering stages: k of n`;
    - stops waiting at `GRID_STAGE_WAIT_S = 2400`;
    - then renders as today, with the stored cache, `tile_identities` and `tile_access`. Any stage whose job failed is a miss and renders here, inside its acquire.
  - **Cancel:** when the parent is cancelled (`handle.check_cancel` raises), it cancels every stage job it submitted that is not terminal.
  - **Local** (`state.storage is None`): no stage jobs; the render is unchanged.

- [ ] **Step 1: Write the failing tests.** They use the in-process job store with fake renders that write segment files.
  - `test_the_parent_queues_one_stage_job_per_planned_stage`
  - `test_the_parent_stitches_after_every_stage_job_and_encodes_no_stage_itself`
  - `test_a_failed_stage_job_is_rendered_by_the_parent`
  - `test_cancelling_the_parent_cancels_its_stage_jobs`
  - `test_a_retried_grid_reuses_the_segments_of_a_killed_parent`: Review Focus 4. Stage jobs succeed, the parent is ended as orphaned, and a second request queues stage jobs that all hit, or skips them.
  - `test_local_grid_queues_no_stage_jobs`
  - `test_no_parent_holds_more_than_one_stage_of_tiles`: it uses the `TileAccess` recorder from Task 2.

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement** as in Interfaces. Also:
  - Grep every `"compare-grid"` in `src/` and decide whether the stage kind belongs beside it: the journal, auto-sync and SPA labels.
  - `ui_static` is a TypeScript edit: run `pnpm typecheck` (`tsc -b`) from `src/splitsmith/ui_static`.
  - Do not run pytest while the SPA build runs.

- [ ] **Step 4: Run** the new file and the full suite. Command: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -q`. Expected: all pass.

- [ ] **Step 5: Commit** `feat(grid): render a hosted grid as one job per stage`

---

### Task 7: Cap 13, rules, What's new, and the staging check

**Files:**
- Modify: `deploy/cloudrun/staging-serve.env.yaml`, `deploy/cloudrun/production-serve.env.yaml`: `SPLITSMITH_WORKER_MAX_EXECUTIONS: "13"`, and update the quota comment to 13 x 4 = 52 vCPU per environment
- Modify: `.claude/rules/hosted.md`: the fan-out paragraph says 13
- Modify: `.claude/rules/rendering.md`: under "Segment cache", add a hosted bullet:
  - tile identities;
  - `StoredSegmentCache` under `matches/<id>/render-segments`;
  - stage jobs plus a stitch;
  - `only_stage`;
  - `TileAccess`;
  - the tests that pin each.

  Add `src/splitsmith/stored_segment_cache.py` to its `paths:`.
- Modify: `src/splitsmith/data/whats_new.json`: one entry through the `whats-new` skill, for example "Whole-match compare grids now render online", in house style.

- [ ] **Step 1: Make the edits.** Run `uv run python scripts/check_claude_md.py` and `uv run pytest -q tests/test_whats_new.py`. Expected: pass.

- [ ] **Step 2: Commit, open the PR, merge on green.** These follow the user's merge rule.

- [ ] **Step 3: Verify on staging.** First check that worker and serve run the merged image digest. Then:

```bash
cd ~/.claude-tmp/bench/wt && echo '{"canvas_width":1920,"canvas_height":1080}' > ../grid-1080.json
uv run python scripts/match_bench.py run ../run-A9.json -o ../result-G1.json --phases grid --grid-options ../grid-1080.json
```

  Expected:
  - the grid phase is quiet within 900 s;
  - no execution hit its memory limit;
  - `compute_jobs` shows 12 `compare-grid-stage` jobs succeeded and the parent succeeded;
  - the MP4 plays, and spot frames from three stages look right.

  Post the numbers on #1691 and #1675.
