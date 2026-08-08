# Per-tile early summary in the compare grid MP4

Date: 2026-08-08
Status: approved, not yet implemented

## Problem

`splitsmith compare render-mp4 --overlay --summary-hold N` renders one MP4
segment per stage. Tiles are beep-aligned, so they finish at different
times: the stage's action runs `head_pad + the longest tile's post-beep
span + tail_pad`, and every tile chain is padded with black to that
length (`mp4_grid._build_filter_graph`, the
`tpad=stop_duration=...:color=black` on each tile).

The result a viewer sees: a shooter who finishes first has a black cell
for the rest of the stage. Only once the *last* tile is done does the
whole canvas cut to the summary still, and every shooter's summary
appears at once.

Wanted instead: a tile shows its own summary from the moment its own
footage ends. Black cells disappear from the render. The requested
`--summary-hold` still begins when the action does -- i.e. when everyone
is finished.

## Approved behaviour

1. While the action runs, each **present** tile's cell is painted with
   that tile's crop of the stage summary still, starting when that
   tile's own footage ends.
2. The end-of-stage hold is unchanged: at `plan.duration_seconds` the
   whole canvas cuts to the summary still and holds for
   `plan.hold_seconds`. For a tile that already switched, this cut is
   pixel-identical and therefore invisible.
3. The action's length is unchanged, tail pad included. The last tile to
   finish therefore shows its summary for `tail_pad + hold_seconds`
   (0.5s + the requested hold, at the CLI's hard-coded pads in
   `compare/cli.py`). Decided deliberately: the alternative -- dropping
   the tail pad when a hold is set -- moves `duration_seconds`, which
   every audio track length, the sprite sequence and the freeze
   extraction key off, for half a second of pacing.
4. Active only when `plan.hold_seconds > 0`. This is the same condition
   under which the summary still is composed today
   (`render_grid_mp4`'s `if plan.hold_seconds > 0:` around
   `_stage_hold_still`). An `--overlay` render with no `--summary-hold`
   comes out byte-identical to what ships now, including its argv.

## Why cropping the existing still is exact

The summary still is one canvas-sized PNG composed by
`overlay_summary.build_hold_still`: each present tile's blurred, dimmed
freeze frame pasted into its cell, then one canvas-wide HTML
rasterization alpha-composited over the lot.

Nothing in it crosses a cell boundary. Text containment is
`overlay_html.grid_html`'s per-cell `overflow: hidden`, declared once in
its stylesheet, and each cell's content is built from that label's own
`TileStageData` alone (`_summary_cells` -> `_cell_groups`). There is no
cross-shooter element: `StagePlacing` / `_rank_placings` compute a rank
but nothing has drawn it since issue #683 Task 8.

So `crop=cell_w:cell_h:col*cell_w:row*cell_h` of the still is byte-for-byte
the cell the hold will show, which is what makes point 2 above hold and
what the integration test asserts.

## Timing

Tile footage end, on the segment timeline:

```
tile_end = tile.lead_pad_seconds + (tile.source_duration_seconds - tile.seek_seconds)
```

Both branches of the plan's seek/pad split collapse to `head_pad +
that tile's post-beep span`, which is where the tile chain's black
`tpad` starts. It is the segment-time expression of the same quantity
`overlay_summary.extract_freeze_frames` already seeks to in clip time.

The switch is armed **one frame early**:

```
enable='gte(t\,{tile_end - 1/canvas.fps})'
```

Arming late by a frame shows a black frame, which is the defect being
fixed. Arming early by a frame covers the tile's own final footage frame
with a blurred, dimmed copy of itself -- indistinguishable. And
`source_duration_seconds` is a probe value, so a sub-frame disagreement
with what actually decodes is expected rather than exceptional; a full
frame of margin absorbs it.

`tile_end - 1/fps` is clamped at `0.0`: a tile whose whole clip is
shorter than one frame must not arm before the segment starts.

## Graph shape

Purely additive. No existing input index, filter or argument moves --
neither on the no-flags path nor on the hold path.

**Input.** One new video input, appended *after* the hold still, which
is currently last:

```
-loop 1 -framerate {rate} -t {plan.duration_seconds} -i {hold_still_path}
```

The same PNG the hold reads, opened a second time rather than `split`
off the hold input. That keeps each input's `-t` meaning exactly one
thing and leaves the hold chain's documented invariant intact ("the
explicit `trim` restates the length the input's `-t` already set, so the
segment's video extent never depends on how `-loop 1` and `concat`'s eof
handling interact"). The cost is decoding one looped PNG twice.

Added under the same condition as the hold input, so `hold_index` keeps
its position and only a new `early_index` follows it.

**Filters.** One `split` into one branch per present tile, then per
tile a crop and an enabled overlay, chained:

```
[{early_index}:v]setpts=PTS-STARTPTS,fps={rate},split={n}[s0][s1]...;
[s0]crop={cw}:{ch}:{col*cw}:{row*ch}[c0];
[prev][c0]overlay={col*cw}:{row*ch}:enable='gte(t\,{arm0})'[e0];
...
```

with the last link labelled for `_video_tail`. A `split=1` degenerate
case (single-tile stage) is written without the `split` rather than
special-cased downstream.

**Position in the chain.** After `_clock_filters`' output, before
`_video_tail`'s `concat`:

```
[grid][ovl]overlay -> [ovlgrid] -> drawtext... -> [ovltext]
  -> early-summary overlays -> [early]
  -> concat with [hold] -> format=yuv420p -> [final]
```

Being after the clock is load-bearing, not incidental. The clock's held
final time is a `drawtext` with `enable='gte(t\,{freeze})'` and no upper
bound (`_clock_filters`, and the docstring explains why the bound is
absent). Composited before the clock, a finished tile would carry its
running-clock time in the corner of its summary cell -- which the hold
never does -- and the cut at `duration_seconds` would then visibly drop
it.

Being before the `concat` preserves `_video_tail`'s guarantee that
nothing drawing on the action can reach a frame of the hold: these
overlays are on the action stream, whose `t` cannot reach the hold
segment.

`_clock_filters` currently both emits the `drawtext` chain and calls
`_video_tail` to close the graph, so the early-summary filters have
nowhere to go between them. It is split: it returns its filters and the
label they end on (`ovlgrid` when there are no clocks, `ovltext` when
there are), and the single caller in `_build_filter_graph` inserts the
early-summary filters and then calls `_video_tail` on whatever label is
current. When there is no hold, or no present tile, nothing is inserted
and the label passed to `_video_tail` is exactly the one it gets today.

## Edges

- **Filler tiles** (`trim_path is None`) and unreached cells stay black
  for the whole stage. There is no source to freeze and
  `build_hold_still` gives a `present=False` cell no text either --
  summary text over black would imply a competitor who was not there.
  Unchanged from the hold's own behaviour today; out of scope here.
- **Freeze extraction failed for a tile.** Its cell in the still is
  black, or black with text if the rasterizer worked. It shows that
  early. Consistent by construction: same pixels as the hold.
- **No Chromium.** The still is blurred freezes with no text. Tiles
  switch to that. Still an improvement on black, and identical to what
  that render's hold already shows.
- **`--no-clock`.** No `drawtext` filters are emitted and the chain
  hangs off `[ovlgrid]`; the label plumbing above handles it with no
  branch of its own.

## Testing

Unit, against the built argv and filter graph:

- The new input is present exactly when the hold input is, and lands
  after it -- existing index assertions must still pass unchanged.
- One overlay per present tile, at that tile's cell offset, with the
  arm time computed above; none for filler tiles or unreached cells.
- The overlays sit after the last `drawtext` and before `concat` in the
  graph string.
- Arm time clamped at `0.0` for a sub-frame clip.
- A zero-hold render's argv is byte-identical to today's.
  `test_zero_hold_produces_the_command_main_produces_today` and
  `test_no_hold_writes_no_still_and_changes_no_command` cover this and
  must pass **unedited**. If either needs a change, the gating is wrong.

Two existing tests in `tests/test_compare_mp4_grid_hold.py` do have to be
extended, because they assert the shape of the input list's tail and a
second `-loop` input now follows the hold's:

- `test_hold_still_input_is_appended_after_the_sprite_input` -- the
  sprite is at `inputs[-3]` now, with the hold still at `-2` and the
  early still at `-1`. Its intent is the one that matters and must be
  kept: nothing may be inserted anywhere but the end, because an input
  in the middle renumbers streams behind it and lands one shooter's
  audio in another's track. The new assertions state the same thing
  about the new input.
- `test_the_still_input_is_looped_for_exactly_the_hold_duration` --
  it slices from the first `-loop` to `-filter_complex`, which now spans
  both inputs. Split into two assertions: the hold input is looped for
  `hold_seconds` exactly as before, and the early input for
  `duration_seconds`.

Neither may be weakened into "some input mentions the still".

Integration, reading actual frames rather than the graph:

- Render two tiles of visibly different length with a hold. Crop the
  short tile's cell region from a frame sampled inside its gap, and the
  same region from a frame sampled during the hold. Assert the two are
  near-identical, and that neither is uniformly black.

That last assertion is the one that matters: it fails against
pre-change code (black vs summary), which is the only proof the fixture
can express the defect. Per the #683 lesson, the test gets run against
the unpatched code before the change is called done.

## Out of scope

- Anything shown in a filler tile's cell.
- Changing the pads, the hold's length semantics, or the summary's
  content and typography.
- The FCPXML export path, which does not run this graph.
