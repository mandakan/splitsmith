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
4. Active only when `plan.hold_seconds > 0` **and** the stage carries an
   overlay plan. The first half is the condition under which the summary
   still is composed today (`render_grid_mp4`'s `if plan.hold_seconds >
   0:` around `_stage_hold_still`). The second is not a convenience:
   `render_grid_mp4` refuses a hold without an overlay outright, so
   hold-without-overlay is a shape `build_stage_command` accepts but no
   render ever produces -- it has no integration coverage and must not
   grow behaviour. An `--overlay` render with no `--summary-hold` comes
   out byte-identical to what ships now, including its argv.

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
handling interact").

**The cost is not "decoding one looped PNG twice."** Measured on a
12-core box, ffmpeg 6.1.1, a 12-tile 4K grid over 10s of action at
`-preset medium -crf 20`, tiles from `testsrc2`:

| | filter-only (`-f null -`) | with libx264 |
|---|---|---|
| base graph | 6.96 s | 22.19 / 22.43 s |
| + early summary | 25.84 s | 33.93 / 34.77 s |

About **+1.9 s of filter work per second of 4K action, ~+53% end to
end**, reproducible across runs. Four qualifying facts, all measured:

- It is paid whether or not any cell arms. The same graph with every
  `enable` forced past the end measured 60.6 s against 65.2 s. `enable`
  only skips the blend; ffmpeg still decodes, scales, splits, crops and
  framesyncs the still for every frame.
- The PNG decode is the minority. Dropping the early input to
  `-framerate 1` and removing `fps=` from that chain recovered 4.7 s of
  the 18.9 s added. The bulk is the N chained `overlay` filters on a 4K
  main frame.
- Linear in tile count, nothing is O(n^2): 1 tile 22.8 s, 3 tiles
  30.7 s, 6 tiles 42.0 s, 12 tiles 65.2 s. The constant is large because
  the default canvas is 4K (`DEFAULT_CANVAS_WIDTH = 3840`) and the CLI
  exposes no override.
- The base uses `testsrc2` sources, which decode far faster than real
  H.264 footage, so with real footage the *ratio* will be smaller. The
  added ~1.9 s/s is a fixed absolute cost that does not shrink.

The `-framerate 1` saving is a separate change needing its own test and
pixel check, and is deliberately not taken here.

Added when the hold input is added *and* an overlay plan is present, so
`hold_index` keeps its position and only a new `early_index` follows it.

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

Three existing tests in `tests/test_compare_mp4_grid_hold.py` do have to
be extended -- exactly the three that pass **both** a hold and an
overlay, which is what the gating above predicts:

- `test_hold_still_input_is_appended_after_the_sprite_input` -- the
  sprite is at `inputs[-3]` now, with the hold still at `-2` and the
  early still at `-1`. Its intent is the one that matters and must be
  kept: nothing may be inserted anywhere but the end, because an input
  in the middle renumbers streams behind it and lands one shooter's
  audio in another's track. The new assertions state the same thing
  about the new input.
- `test_the_sprite_overlay_does_not_reach_the_hold` -- asserts the
  `concat`'s first input is `[ovlgrid]` or `[ovltext]`. It is now the
  last early-summary label. The claim being kept is that the sprite is
  composited inside the half that ends at the freeze, so the assertion
  becomes "the join's first input is whatever the action chain ends on,
  and the sprite chain precedes it".
- `test_the_hold_does_not_touch_the_clock_windows` -- it collects every
  comma-separated part containing `drawtext=` **or** `enable=`, and the
  early overlays carry an `enable=`. Narrow the predicate to
  `drawtext=`; the surrounding `graph.count("lt(t\\,") == 1` assertion
  already covers the claim that nothing else introduces an upper bound.

None may be weakened into "some input mentions the still" or "some
filter has an enable".

The remaining hold tests pass unedited, including
`test_the_still_input_is_looped_for_exactly_the_hold_duration`,
`test_hold_is_concatenated_after_the_action_not_composited_over_it` and
`test_the_hold_does_not_move_the_beep` -- all three build their command
without an overlay.

Integration, reading actual frames rather than the graph. The fixture
already has what this needs: `tests/compare_fixture.py` gives Mathias a
deliberately shorter clip so his cell is black for the last stretch of
every action, and exports `SHORT_FOOTAGE_ENDS` as the segment time where
it runs out.

**Two existing assertions in
`test_the_summary_hold_reaches_the_rendered_pixels` assert the behaviour
being removed, and both must be inverted rather than deleted:**

- `SHORT_TILE_MIN_BLACK_FRACTION` -- "Mathias's cell at `PICTURE_INDEX`
  ... measured 0.804 black". After the change his cell shows his summary
  there. The inverted form is the new instrument: that cell now matches
  the same cell of the still the render composed.
- `TAIL_PAD_MIN_BLACK_FRACTION` -- "the last action frame is inside the
  tail pad and therefore black on every tile". With the tail pad kept
  (decision 3), every present tile is showing its summary by then. The
  inverted form is stronger: the whole last action frame now matches the
  composed still, which is the same measure the in-hold check already
  uses.

Also moved rather than dropped: the clock check at the last action frame
compares a shooter's clock corner against Bea's *in the same frame*, and
that frame is now summary on every tile. It samples a frame where both
clocked tiles still have live footage instead.

New assertions:

- At `PICTURE_INDEX`, the short tile's cell matches its cell of the
  composed still, and is not black.
- At the last action frame, the whole canvas matches the composed still.
- **At a frame a few before the short tile's arm time, that same cell is
  still live footage** -- i.e. far from the still. Without this, an
  `enable` expression that is simply always true passes every other
  check here.
- The unreached cell is still black at `PICTURE_INDEX`.

Thresholds are measured against this fixture and pinned with the
measurement in the comment, matching the module's existing convention --
not guessed.

Every new or inverted assertion is run against the unpatched renderer
before the change is called done. Per the #683 lesson, a fixture that
cannot express the defect is the failure mode to guard against, and
here the old constants are proof the fixture can.

## Out of scope

- Anything shown in a filler tile's cell.
- Changing the pads, the hold's length semantics, or the summary's
  content and typography.
- The FCPXML export path, which does not run this graph.
