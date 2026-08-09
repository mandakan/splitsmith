# Hold Still at Composed Size (#691) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Subagent model tiers:** Tasks 1-3 are mechanical once this plan is followed exactly - run them on sonnet. The Task 4 review pass needs the session model.

**Goal:** `render_grid_mp4` with a summary hold succeeds on any canvas, by composing every summary still at the grid's composed size (`cols * cell_w` x `rows * cell_h`) instead of the canvas size.

**Architecture:** `mp4_grid._cell_size` floor-divides, so a canvas that does not divide by the grid makes `xstack` compose smaller than the canvas (1280 at 3 cols -> 1278). The hold still is composed at canvas size and joined via `concat`, which refuses the size mismatch at graph-config time - every stage fails. Fix (option 1 from the issue, confirmed with the user): a new `_composed_size` helper feeds three places - the `SpriteGeometry` that `_stage_hold_still` constructs (so the PNG itself shrinks; `overlay_summary.py` is untouched because `SpriteGeometry` floor-divides back to identical cell sizes), the hold chain's `scale=`, and the early-summary chain's `scale=`. The now-redundant guard in `scripts/render_grid_frames.py` is removed.

**Tech Stack:** Python 3.11+, ffmpeg filter graphs, PIL, pytest (`-m integration` for real-ffmpeg tests).

## Global Constraints

- Work on branch `fix/691-hold-still-composed-size` (create it from current `main` before Task 1; use a worktree if executing via superpowers:using-git-worktrees).
- `uv` for everything; never `pip`. Run tests as `uv run pytest ...`.
- Black line length 110; ruff must pass. Run both before every commit (CI gate).
- New comments/docstrings use a single ASCII "-", never "--" and never an em dash. Grep your added lines before committing: `git diff --cached | grep '^+' | grep -E '—|--'` (hits inside code/filter strings like `--summary-hold` or `-filter_complex` are fine; prose is not).
- Do not touch the sprite path (`_stage_overlay_plan`, `overlay_live.py`): sprite sheets stay canvas-sized on purpose. They composite via `overlay=0:0`, which tolerates the mismatch (empirically confirmed - overlay-without-hold renders pass on a non-dividing canvas today), and shrinking them changes cached-sprite content hashes for zero behavior gain.
- An odd composed dimension (e.g. canvas 1281 at 3 cols -> 1281) still breaks libx264. Pre-existing for plain grids, out of scope here - do not add handling for it.
- Integration tests must not skip in CI: build media with the existing fixtures (`shooter_clips` / `_roster`), never a gitignored sample.

## File Structure

- Modify `src/splitsmith/compare/mp4_grid.py` - all four production changes live here:
  - new `_composed_size` next to `_cell_size` (line ~677)
  - `_early_summary_filters` scale (line ~1106)
  - `_build_filter_graph` hold-chain scale + stale comment (lines ~1521-1529)
  - `_stage_hold_still` geometry (lines ~1868-1876)
- Modify `scripts/render_grid_frames.py` - delete the divisibility guard (lines ~379-400).
- Test `tests/test_compare_mp4_grid_hold.py` - command-string test, no ffmpeg.
- Test `tests/test_compare_grid_overlay_integration.py` - rendered-and-measured test, real ffmpeg.

No new files. `overlay_summary.py`, `overlay_sprites.py`, `overlay_live.py` are deliberately untouched.

---

### Task 1: Compose stills and their filter chains at the composed size

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py:677-679` (add helper below `_cell_size`)
- Modify: `src/splitsmith/compare/mp4_grid.py:1106` (`_early_summary_filters`)
- Modify: `src/splitsmith/compare/mp4_grid.py:1521-1529` (`_build_filter_graph` hold chain)
- Modify: `src/splitsmith/compare/mp4_grid.py:1868-1876` (`_stage_hold_still`)
- Test: `tests/test_compare_mp4_grid_hold.py`

**Interfaces:**
- Consumes: `_cell_size(canvas: GridCanvas, plan: GridStagePlan) -> tuple[int, int]` (existing, unchanged).
- Produces: `_composed_size(canvas: GridCanvas, plan: GridStagePlan) -> tuple[int, int]` returning `(cell_w * plan.cols, cell_h * plan.rows)`. Task 2's integration test relies on the hold PNG (`summary-stage<N>.png`) now being written at this size.

- [ ] **Step 1: Write the failing command-string test**

Append to `tests/test_compare_mp4_grid_hold.py` (helpers `_plan`, `_graph_of`, `_chains`, `_overlay_plan`, `HOLD`, `HOLD_STILL` already exist in the file; `_command` hardcodes a dividing 1920x1080 canvas, so call `build_stage_command` directly):

```python
def test_a_non_dividing_canvas_scales_the_stills_to_the_composed_size(tmp_path):
    """#691: the still must agree with what xstack composes, not the canvas.

    1280 at 3 columns floors to 426-wide cells, so the grid is 1278 wide.
    A canvas-sized still fails ``concat`` at graph-config time and every
    stage dies. Both consumers of the PNG - the hold chain and the early
    per-tile summary chain - must scale to the composed size.
    """
    plan = _plan(("Ann", "Bo", "Cy", "Di", "Ed"), rows=3, cols=3, hold=HOLD)
    cmd = mp4_grid.build_stage_command(
        plan,
        canvas=mp4_grid.GridCanvas(1280, 720, 30, 1),
        output_path=Path("/w/s3.mov"),
        ffmpeg_binary="/bin/ffmpeg",
        overlay=_overlay_plan(tmp_path),
        hold_still_path=HOLD_STILL,
    )
    graph = _graph_of(cmd)

    (still_chain,) = _chains(graph, r"\[hold\]$")
    assert "scale=1278:720" in still_chain, still_chain
    # The early-summary chain reads the same PNG and crops cells out of
    # it; a canvas-sized scale there leaves the crop grid misaligned by
    # the flooring remainder.
    assert graph.count("scale=1278:720") == 2, graph
    assert "scale=1280:720" not in graph, graph
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_compare_mp4_grid_hold.py::test_a_non_dividing_canvas_scales_the_stills_to_the_composed_size -n0 -v`
Expected: FAIL - the hold chain currently contains `scale=1280:720`.

- [ ] **Step 3: Implement**

3a. Add below `_cell_size` (after line 679):

```python
def _composed_size(canvas: GridCanvas, plan: GridStagePlan) -> tuple[int, int]:
    """What ``xstack`` actually composes: the floored cells re-multiplied.

    Equal to the canvas whenever it divides by the grid; up to
    ``cols - 1`` / ``rows - 1`` pixels smaller when it does not. Every
    still that meets the composed video - the hold via ``concat``, the
    early per-tile summary via per-cell crops - must be this size, not
    the canvas's (#691).
    """
    cell_w, cell_h = _cell_size(canvas, plan)
    return cell_w * plan.cols, cell_h * plan.rows
```

3b. In `_early_summary_filters`, add `composed_w, composed_h = _composed_size(canvas, plan)` next to the existing `cell_w, cell_h = _cell_size(canvas, plan)` at line 1097, and change the scale on line 1106 from:

```python
        f"[{early_index}:v]setpts=PTS-STARTPTS,scale={canvas.width}:{canvas.height},"
```

to:

```python
        f"[{early_index}:v]setpts=PTS-STARTPTS,scale={composed_w}:{composed_h},"
```

3c. In `_build_filter_graph`, the hold chain (line ~1528) and its comment (lines ~1518-1524). `cell_w, cell_h` are in scope from line 1465. Change:

```python
    # The still, conformed to exactly what ``concat`` compares: size, SAR
    # and frame rate. ``scale`` is a no-op on a still this module wrote
    # (``build_hold_still`` composes at canvas size) and the guard against
```

to:

```python
    # The still, conformed to exactly what ``concat`` compares: size, SAR
    # and frame rate. ``scale`` is a no-op on a still this module wrote
    # (``build_hold_still`` composes at the composed grid size, which is
    # what ``xstack`` emits - see ``_composed_size``) and the guard against
```

and the filter itself (with `composed_w, composed_h = _composed_size(canvas, plan)` added near the existing `cell_w, cell_h` assignment at line 1465) from:

```python
            f"[{hold_index}:v]setpts=PTS-STARTPTS,scale={canvas.width}:{canvas.height},"
```

to:

```python
            f"[{hold_index}:v]setpts=PTS-STARTPTS,scale={composed_w}:{composed_h},"
```

3d. In `_stage_hold_still` (lines 1868-1876), change the geometry from canvas size to composed size:

```python
    composed_w, composed_h = _composed_size(canvas, plan)
    return write_hold_still(
        plan,
        _overlay_data_for_stage(data, plan.stage_number),
        # Composed size, not canvas size: ``SpriteGeometry`` floor-divides
        # its width and height back into cells, and a composed size divides
        # exactly, so the cells come out identical to ``_cell_size``'s and
        # the PNG matches the ``xstack`` output by construction (#691).
        SpriteGeometry(
            canvas_width=composed_w,
            canvas_height=composed_h,
            rows=plan.rows,
            cols=plan.cols,
        ),
        theme=load_theme(theme_name),
        work_dir=work,
        ffmpeg_binary=ffmpeg_binary,
        runner=runner,
        rasterizer=rasterizer,
    )
```

- [ ] **Step 4: Run the new test and the whole hold/overlay unit suite**

Run: `uv run pytest tests/test_compare_mp4_grid_hold.py tests/test_compare_mp4_grid_overlay.py -v`
Expected: all PASS. The existing `test_hold_is_concatenated_after_the_action_not_composited_over_it` asserts `scale=1920:1080` on a 2x2 - that divides exactly, so composed == canvas and it must still pass. If it fails, the helper's arithmetic is wrong; do not edit the existing test to make it pass.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_hold.py
git commit -m "fix(compare): compose summary stills at the grid's composed size (#691)"
```

---

### Task 2: Rendered-and-measured integration test on a non-dividing canvas

**Files:**
- Test: `tests/test_compare_grid_overlay_integration.py` (append after `test_the_summary_hold_reaches_the_rendered_pixels`, the current last function)

**Interfaces:**
- Consumes: Task 1's behavior - `work_dir/summary-stage1.png` written at composed size; segment renders. Also existing module members of the test file: `_roster`, `shooter_clips` fixture, `_frame_at_index`, `_mean_abs_diff`, `_video_frames`, `integration`, `needs_ffmpeg`, `FFMPEG`, `HEAD_PAD_SECONDS`, `TAIL_PAD_SECONDS`, `HOLD_SECONDS`, `SEGMENT_FRAMES`, `MID_HOLD_INDEX`, `HOLD_MATCHES_ITS_STILL_MAX`.
- Produces: nothing downstream.

- [ ] **Step 1: Write the test**

The issue's review note is explicit: filter-graph work fails silently, so this must be a rendered-and-measured check, not an exit-code check. Canvas 641x361 at the 3-shooter 2x2 roster composes 640x360 - both dimensions non-dividing, composed dimensions even (libx264-safe), and it reuses the module-scoped clips so no new fixture cost. Same 30fps and pads as `CANVAS`, so the module's frame-index constants stay valid.

```python
@integration
@needs_ffmpeg
def test_a_non_dividing_canvas_still_renders_the_hold(tmp_path: Path, shooter_clips):
    """#691, rendered and measured: 641x361 at 2x2 composes 640x360.

    Before the fix the hold still was canvas-sized, ``concat`` refused
    the 641x361-vs-640x360 mismatch at graph-config time and every stage
    failed. Exit 0 alone is not the instrument - the claim is that the
    still, the decoded frames and the hold's pixels all agree at the
    composed size.
    """
    canvas = mp4_grid.GridCanvas(width=641, height=361, frame_rate_num=30, frame_rate_den=1)
    composed = (640, 360)
    shooters = _roster(tmp_path, shooter_clips, stages=1)
    out = tmp_path / "odd-canvas.mp4"
    result = mp4_grid.render_grid_mp4(
        shooters,
        audio_label="Mathias",
        output_path=out,
        canvas=canvas,
        head_pad_seconds=HEAD_PAD_SECONDS,
        tail_pad_seconds=TAIL_PAD_SECONDS,
        overlay=True,
        summary_hold_seconds=HOLD_SECONDS,
        ffmpeg_binary=FFMPEG,
        work_dir=tmp_path / "work-odd",
    )
    assert result.failed == (), result.failed
    assert out.exists()

    # The still itself shrank to the composed size.
    still = Image.open(tmp_path / "work-odd" / "summary-stage1.png").convert("RGB")
    assert still.size == composed, still.size

    # concat accepted the join: the decoded stream is action + hold, at
    # the composed size.
    assert _video_frames(out) == SEGMENT_FRAMES
    in_hold = _frame_at_index(out, MID_HOLD_INDEX, tmp_path, "odd-hold")
    assert in_hold.size == composed, in_hold.size

    # And the hold's pixels are the composed still's - the whole-frame
    # check the two-stage test calls THE instrument.
    whole = (0, 0, *composed)
    to_still = _mean_abs_diff(in_hold, still, whole)
    assert to_still <= HOLD_MATCHES_ITS_STILL_MAX, (
        f"the hold is not showing the still this render composed: mean abs diff "
        f"{to_still:.2f} over the composed frame (threshold {HOLD_MATCHES_ITS_STILL_MAX})"
    )
```

Before running: check the module's actual constant names near the top of the file (`SEGMENT_FRAMES`, `MID_HOLD_INDEX`) - the two-stage test asserts `held_frames == 2 * SEGMENT_FRAMES` with message "2 x ACTION_FRAMES action + 2 x HOLD_FRAMES hold", so one stage with a hold is `SEGMENT_FRAMES` frames. If the names differ from this plan, use the file's names; the arithmetic intent is action-plus-hold frames for one stage.

- [ ] **Step 2: Run it against the fixed code**

Run: `uv run pytest tests/test_compare_grid_overlay_integration.py::test_a_non_dividing_canvas_still_renders_the_hold -n0 -m integration -v`
Expected: PASS (needs real ffmpeg on PATH; if it skips locally, something is wrong with the environment - do not commit a skipping test).

- [ ] **Step 3: Prove the test catches the bug**

Revert the production fix, watch the test fail, restore:

```bash
git stash push src/splitsmith/compare/mp4_grid.py
uv run pytest tests/test_compare_grid_overlay_integration.py::test_a_non_dividing_canvas_still_renders_the_hold -n0 -m integration -v
git stash pop
```

Expected while stashed: FAIL with `result.failed` non-empty (GridRenderError text mentions every stage failing). If it PASSES while stashed, the test is not an instrument - stop and fix the test, per the review practice in CLAUDE.md.

- [ ] **Step 4: Run the full integration file**

Run: `uv run pytest tests/test_compare_grid_overlay_integration.py -n0 -m integration -v`
Expected: all PASS (the module-scoped clip fixture is shared; `-n0` keeps one worker on it).

- [ ] **Step 5: Commit**

```bash
git add tests/test_compare_grid_overlay_integration.py
git commit -m "test(compare): render-and-measure the hold on a non-dividing canvas (#691)"
```

---

### Task 3: Remove the script guard and verify visually

**Files:**
- Modify: `scripts/render_grid_frames.py:379-400`

**Interfaces:**
- Consumes: Task 1's fix (the guard's reason to exist).
- Produces: nothing downstream.

- [ ] **Step 1: Delete the guard**

Remove the whole block - the comment (lines 379-389) and the `rows, cols = ...` / `width, height = ...` / `parser.error(...)` guard (lines 390-400) - and replace with:

```python
    # A canvas that does not divide by the grid composes slightly smaller
    # (``mp4_grid._cell_size`` floors - 1280x720 at 3x3 renders 1278x720).
    # Since #691 the summary stills are composed at that same size, so the
    # combination just works; the output is the composed size, not the
    # requested canvas.
```

Check whether `grid_shape` / `choose_grid` are still used elsewhere in the script (`grep -n "grid_shape\|choose_grid" scripts/render_grid_frames.py`); if the guard was their only consumer, remove them from the import at line ~70 too, or ruff's F401 will fail the gate.

- [ ] **Step 2: Run the exact combination from the issue**

Run: `uv run python scripts/render_grid_frames.py --shooters 5 --canvas 1280x720 --overlay --summary-hold 2 --keep-video`
Expected: exit 0, frames written (the issue's repro; it previously died with "every stage failed to render").

- [ ] **Step 3: Look at the output**

Read the rendered frames (they are PNGs in the script's output directory, printed on completion) - specifically `hold-start`, `hold-mid` and `hold-end`. Confirm with your own eyes: the frozen, blurred, dimmed summary is present, cells are aligned with no seam or stretch at the right edge, text is drawn. Then confirm geometry:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 <output-dir>/grid.mp4
```

Expected: `1278,720`. (Use the actual MP4 path the script prints; `--keep-video` keeps it.)

- [ ] **Step 4: Commit**

```bash
git add scripts/render_grid_frames.py
git commit -m "chore(compare): drop the render-frames divisibility guard (#691)"
```

---

### Task 4: Gates, review pass, PR

**Files:** none new - verification and shipping.

- [ ] **Step 1: Full local CI gate**

```bash
uv run ruff check .
uv run black --check .
uv run pytest
uv run pytest -m integration -n0
```

Expected: all clean. Fix anything that is not - there is no pre-existing debt excuse in this repo.

- [ ] **Step 2: Review pass (session model, per CLAUDE.md review practice)**

Dispatch a reviewer subagent over the whole branch diff with these named claims to verify, telling it the implementation report is unverified:

1. "Passing a composed size into `SpriteGeometry` yields `cell_width`/`cell_height` identical to `mp4_grid._cell_size`'s for every rows/cols" - check the floor-division arithmetic in `overlay_sprites.py:267-273` against `mp4_grid.py:679`, and hunt for any diverging input.
2. "Every consumer of the hold PNG scales/crops at the composed size" - the hold chain, the early-summary chain, and any call site of `write_hold_still`. A missed consumer fails silently or off-by-two, not loudly.
3. "Each new test genuinely fails against the pre-change code" - re-run the Task 2 Step 3 stash check for the integration test AND the Task 1 unit test.
4. One whole-branch pass over the seams: guard removal vs script comments/docstrings that still claim the limitation, stale mentions of "canvas size" near the still (`grep -n "canvas size" src/splitsmith/compare/mp4_grid.py`), and the `overlay_summary.build_hold_still` docstring line "Compose the canvas-sized RGB stage summary still" - if it now misleads, fix the wording.

- [ ] **Step 3: Apply findings, re-run the gate, push and open the PR**

```bash
git push -u origin fix/691-hold-still-composed-size
gh pr create --title "fix(compare): compose summary stills at the grid's composed size" \
  --body "Fixes #691. Stills (hold + early per-tile summary) now compose at cols*cell_w x rows*cell_h, so concat and the per-cell crops agree with the xstack output on any canvas. Drops the render_grid_frames divisibility guard. Partially unblocks #692 (2.7K needs this; see the corrected blocked-on note there)."
```

Then watch CI to green before merging: `gh run watch` (main has no required checks, so do not `--auto` merge before it is green).

- [ ] **Step 4: After merge, comment on #692**

Note on #692 that the #691 blocker is resolved and arbitrary canvases are safe for the hold path (output is the composed size; the CLI canvas flag remains #692's own scope).
