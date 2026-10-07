# MP4 Transitions on the Boundary Segment (compare grid) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `splitsmith compare export --format mp4 --transition fade` and the Export page's grid mode render the same centred crossfade between stages that the single-shooter MP4 renders since PR #1255, with the grid's N+1 audio layout intact and the stitch still a stream copy.

**Architecture:** The grid driver gets a spine like `mp4_render.TimelinePlan`: title page, per stage a slate (when `stage_titles == "slate"`) and the stage segment (action plus hold), the closing card; the stage-indexed transitions become boundaries between consecutive spine items with `head_cut` / `tail_cut` on the items, placed by the same fit rule (d/2 within the head pad; d/2 within the hold plus the tail pad; a card at least d long; report, never clamp). A stage's cuts and edges are *plan-level*: `narrow_grid_plan` recomputes every tile's `seek` / `lead_pad` for a later head (`head_pad' = head_pad - head_cut`, the head pad recovered from any real tile as `lead + beep - seek`), takes a tail cut from the hold first and then the action, and `_stage_overlay_plan` is fed the narrowed head pad and duration so clocks and sprite states move with it. Edges are rendered with `build_stage_command` on an edge plan (so the edge carries the grid's own N+1 PCM tracks); a tail edge that lies entirely in the hold is a still segment of the hold PNG (`build_card_segment_command`). The handle is what every real tile's trim holds before the pad (`min(tile.seek_seconds)`) or after the action (`min(source - (seek + duration - lead))`, zero when there is a hold); the boundary pads the rest by holding the edge's end frame. The boundary command crossfades the video with `xfade` and *each* of the N+1 audio tracks with `acrossfade` (`[0:a:k][1:a:k]`), re-emits the grid's disposition and track names, and is a PCM `.mov` like every other segment. Edge and boundary encodes go through a new `boundary_runner` (default `subprocess.run`), never `runner`, which both CLIs count for "stage N of M".

**Scope:** `compare/mp4_grid.py`, its request layer (`CompareGridRequest`, `exportCompareGrid`, `compare export --transition`), the gallery (`modes: ["single", "compare"]` on the xfade variants, `transitionsSupported("mp4", "grid")`), and `scripts/render_grid_frames.py`. The grid has no segment cache; none is added.

**Tech Stack:** Python 3.12, ffmpeg `xfade` / `acrossfade` / `tpad` / `apad` / `adelay`, pytest, vitest, the grid frame script.

**Spec:** `docs/superpowers/specs/2026-10-06-rendered-video-first-design.md` section 3 (with the slice 4 amendment). Issue #1244 (this PR closes it).

## Global Constraints

- Python 3.11+, type hints, Black 110, Ruff; no new dependencies.
- The no-transition grid argv is byte-identical: `tests/test_compare_mp4_grid_commands.py::test_the_default_off_argv_is_unchanged_since_the_preflight_landed` (`DEFAULT_OFF_ARGV_SHA256`) stays green, and the grid's 25 default frames stay pixel-identical to main.
- Every edge and boundary encode goes through `boundary_runner`; `runner` sees exactly `len(plans)` stage encodes plus the stitch (`tests/test_compare_mp4_grid_render.py` runner-count tests).
- Every segment the stitch reads has one video stream and N+1 PCM audio tracks in the order Mix, then the shooters, with `_disposition_args` and `_track_naming_args`.
- Degradation policy: a transition never fails a render; a miss or a failed edge is a cut plus an `OverlayDegradation(summary, detail)` in `GridRenderResult.degradations`.
- Run pytest and the frame scripts with the project's static ffmpeg first on PATH.

## Review Focus

1. A tile whose footage ends before the stage (the "short tile", black from its end) must stay black through a tail edge and the boundary; the padding holds the *composed* edge's last frame, which is already black for that tile. Test in Task 3 (argv) and the frame look in Task 7.
2. The overlay clocks must read the same time at a given source frame before and after a head cut: `start_seconds` moves by exactly the cut. Test in Task 2 (`test_a_head_cut_moves_the_clocks_by_the_cut`).
3. A hold shorter than d/2: the tail edge is `half - hold` of action plus the hold, with the early-summary arms and the hold still in the right place. Test in Task 3.
4. A boundary with a filler tile (no trim): its silent track crossfades into the next stage's track for that slot without an `acrossfade` error. Test in Task 4 (argv names `a:k` for every k) and the real-ffmpeg test in Task 6.
5. The compare CLI's progress ("stage N of M") must not count edges or boundaries. Test in Task 5.

---

### Task 1: The grid's spine and boundaries (pure)

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py` (new `GridItem` kinds, `GridBoundary`, `plan_grid_spine`, `grid_boundary_fit`, `head_pad_of`)
- Test: `tests/test_compare_mp4_grid_spine.py` (new)

**Interfaces:**
- `GridStageItem(plan: GridStagePlan, index: int, head_cut_seconds=0.0, tail_cut_seconds=0.0)`; `GridCardItem(kind: Literal["title_page","slate","closing"], name: str, card, card_seconds: float, stage_index: int | None, head_cut_seconds=0.0, tail_cut_seconds=0.0)`; both expose `duration_seconds` (shown length) and `name`.
- `GridBoundary(after_index: int, kind: TransitionKind, duration_seconds: float)` with `.name`.
- `plan_grid_spine(plans, *, title_page, closing, stage_titles, title_duration_seconds, transitions) -> GridSpine(items, boundaries, degradations)`.
- `head_pad_of(plan) -> float`: `lead + beep - seek` of the first real tile (0.0 when every tile is filler).
- `grid_boundary_fit(prev, nxt, *, half, seconds, tail_pad_seconds) -> str | None`: a stage before the cut needs `half <= plan.hold_seconds + tail_pad_seconds`; a stage after needs `half <= head_pad_of(plan)`; a card needs `half <= card_seconds / 2`. Messages mirror `mp4_render._boundary_fit`.

- [ ] Steps: failing tests for the spine order with slates, the boundary between a stage and the next slate, the fit refusals (hold + tail pad, head pad, card), no transitions -> no boundaries; implement; GREEN; commit `feat(grid-transitions): the grid spine and its boundaries (#1244)`.

---

### Task 2: Narrowing a grid plan and the overlay with it

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py` (`narrow_grid_plan`, `_stage_overlay_plan` takes the narrowed head pad and duration from the plan it is given), `src/splitsmith/compare/overlay_sprites.py` (no change expected; pin that `_state_starts` stays positive under the fit rule)
- Test: `tests/test_compare_mp4_grid_hold.py`, `tests/test_compare_mp4_grid_overlay.py`

**Interfaces:**
- `narrow_grid_plan(plan, *, head_cut: float, tail_cut: float) -> GridStagePlan`: for each real tile `head_pad' = head_pad_of(plan) - head_cut`, `seek' = max(0, beep - head_pad')`, `lead' = max(0, head_pad' - beep)` (the inset fields alike); `hold' = max(0, hold - tail_cut)`, `duration' = duration - head_cut - max(0, tail_cut - hold)`; fillers unchanged. Negative `head_cut` reads handle footage (a larger seek is just a later start of the same tile: for a handle the seek *decreases*, bounded at 0 by the handle rule in Task 3).
- `_stage_overlay_plan(...)` keeps its signature; callers pass `head_pad_seconds=head_pad_of(narrowed)` and the narrowed plan, so clocks start at `head_pad - head_cut` and sprite states are built over `duration'`.

- [ ] Steps: failing tests: `narrow_grid_plan` seeks/leads for a lead-padded and a seeked tile, a tail cut from the hold first then the action, zero cuts return an equal plan (and `build_stage_command` argv byte-identical), `test_a_head_cut_moves_the_clocks_by_the_cut`; implement; GREEN; commit `feat(grid-transitions): narrowing a grid plan moves its tiles, hold and overlay (#1244)`.

---

### Task 3: Edge plans, handles and the hold-only edge

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py` (`grid_edge_handle`, `grid_edge_plan`, `grid_edge_is_hold_only`)
- Test: `tests/test_compare_mp4_grid_hold.py`

**Interfaces:**
- `grid_edge_handle(plan, *, half, end) -> float`: head: `min(half, min(tile.seek_seconds for real tiles))` (0 with a lead-padded tile); tail: `0.0` when `plan.hold_seconds > 0`, else `min(half, min(source - (seek + duration - lead) for real tiles))`, floored at 0.
- `grid_edge_plan(plan, *, half, end) -> GridStagePlan`: head: `narrow_grid_plan(plan, head_cut=-handle, tail_cut=plan.total_seconds - half)`; tail with `hold < half`: `narrow_grid_plan(plan, head_cut=plan.duration_seconds - (half - hold), tail_cut=-handle)`; tail with `hold >= half`: not a plan, see `grid_edge_is_hold_only(plan, half) -> bool` (the driver renders a still of the hold PNG for `half` seconds through `build_card_segment_command`).

- [ ] Steps: failing tests for each formula (default fixture: head pad 1.0, tail pad 0.5, hold 3.0 and 0.25, a short tile), the short tile's black tail inside a tail edge (Review Focus 1: the tile chain's `tpad ... color=black` still spans the edge), implement, GREEN, commit `feat(grid-transitions): edge plans with per-tile handles (#1244)`.

---

### Task 4: The grid boundary command

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py` (`build_boundary_segment_command`)
- Test: `tests/test_compare_mp4_grid_cards.py`

**Interfaces:**
- `build_boundary_segment_command(tail_edge: Path, head_edge: Path, *, kind, seconds, canvas, shooter_labels, output_path, ffmpeg_binary="ffmpeg", tail_pad_seconds=0.0, head_pad_seconds=0.0) -> tuple[str, ...]`. Graph: optional `[0:v]tpad=stop_mode=clone:stop_duration=P[tv]` / `[1:v]tpad=start_mode=clone:start_duration=P[hv]`; `[tv][hv]xfade=transition=<xfade_name(kind)>:duration=S:offset=0,format=yuv420p[final]`; for `k in 0..N`: optional `[0:a:k]apad=pad_dur=P[t{k}]` / `[1:a:k]adelay=ms:all=1[h{k}]`, then `[t{k}][h{k}]acrossfade=d=S:c1=tri:c2=tri,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[x{k}]`. Maps `[final] [x0] .. [xN]`, `_disposition_args(audio_track_labels(labels), 0)`, `_track_naming_args`, `-r rate`, `-c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -c:a pcm_s16le`, `-t S`, output `.mov`.

- [ ] Steps: failing argv test (three shooters, one filler: four `acrossfade` chains named `a:0..a:3`, maps and naming as the card segment's), the padded variants, implement, GREEN, commit `feat(grid-transitions): the grid boundary segment (#1244)`.

---

### Task 5: The driver, the runner, the request layer and the CLI

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py` (`render_grid_mp4(..., transitions=(), boundary_runner=subprocess.run)`: the per-stage loop walks `plan_grid_spine`; per item it prepares (overlay plan, hold still, lower third, free still) once, decides a boundary before encoding the item that opens it (tail edge, head edge, boundary through `boundary_runner`; any failure clears both cuts and records an `OverlayDegradation`), encodes the item narrowed, appends the boundary; `elapsed` and chapters follow the shown durations; a lower third on a stage after a boundary gets `LowerThirdInput(delay/skip)` like mp4_render, dropped when the head edge showed it in full), `src/splitsmith/compare/cli.py` (`--transition`, `--transition-seconds`, the progress runner unchanged), `src/splitsmith/ui/exports_api.py` (`CompareGridRequest.transition_kind` / `transition_duration_seconds`), `src/splitsmith/ui/server.py` (`_run_compare_grid` threads them; `_compare_grid_progress_runner` unchanged), `src/splitsmith/ui_static/src/lib/api.ts` (payload fields), `lib/renderOptions.ts` (`transitionsSupported("mp4", "grid")` true), `lib/lookGallery.ts` (xfade variants `modes: ["single", "compare"]`), `pages/Export.tsx` / `pages/matchExportModel.ts` (the grid payload carries `visibleTransitionKind`), `lib/exportPresets.ts` (the summary names a grid transition)
- Test: `tests/test_compare_mp4_grid_render.py` (driver order, cut on failure, runner counts: Review Focus 5), `tests/test_compare_cli_mp4.py`, `tests/test_compare_grid_endpoint.py`, `ui_static` tests (`lookGallery.test.ts` grid visibility, `api.compareGrid.test.ts`, `Export` page tests through `openGroups`)

- [ ] Steps: failing tests first for each seam; implement; GREEN; commit `feat(grid-transitions): the grid driver renders boundaries; CLI, API and gallery carry them (#1244)`.

---

### Task 6: Real ffmpeg: a two-shooter grid with a fade

**Files:**
- Create: `tests/test_compare_mp4_grid_transitions_integration.py` (`@pytest.mark.integration`, skipped without ffmpeg): two shooters, two stages, a 1 s fade, hold 2 s; probe the boundary `.mov` (1 s, N+1 audio streams), the stitched length equals the cut's within two frames, and every audio stream of the output exists with the right titles (`ffprobe -show_streams`).

- [ ] Steps: write, run with the project ffmpeg, commit `test(grid-transitions): a real two-shooter fade keeps the length and the tracks (#1244)`.

---

### Task 7: Frames, docs

**Files:**
- Modify: `scripts/render_grid_frames.py` (`--transition`, `--transition-seconds`; `_moments` subtracts each cut from the neighbouring segment lengths and adds `boundary-N-in/mid/out`), `CLAUDE.md` (the grid sentence in the transitions paragraph), `SPEC.md` (`compare/mp4_grid.py` line), the spec amendment's last sentence.

- [ ] Steps: render `--title-page --closing-card --titles slate --overlay --summary-hold 2 --transition fade --transition-seconds 1` and `--summary-hold 0 --transition dissolve` with three shooters; look at the boundary frames (Review Focus 1: the short tile stays black); re-render the default grid set and diff against main (25 of 25); publish; full suite; commit `feat(grid-transitions): boundary frames, docs (#1244)`.
