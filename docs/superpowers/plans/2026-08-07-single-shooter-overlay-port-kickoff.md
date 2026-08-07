# Issue #684 kickoff -- the single-shooter export joins the overlay engine

Entry point for a session starting fresh on #684. Assumes no memory of
the sessions that built the compare-grid overlay, the composition seam
(#683) or the live-sprite port (#693).

**The issue:** https://github.com/mandakan/splitsmith/issues/684 -- read
it, but read the corrections below first. It was written before #683 and
#693 landed, and three of its statements are now stale in ways that
change the work.

## Why this exists

`splitsmith export overlay` renders a **standalone transparent MOV** per
stage -- ProRes 4444 or hevc-alpha -- that drops onto the trimmed clip in
Final Cut as a connected clip on V2. It is not burned into the picture the
way the compare grid's overlay is. Same information, different delivery.

That renderer (`src/splitsmith/overlay_render.py`, 607 lines) predates
everything the grid learned. It draws **every frame** with PIL and pipes
raw RGBA bytes to ffmpeg, it has no stage summary, and it cannot draw
scoring at all.

## Correct the premise before you start

### Stale -- "exactly one module is shared"

The issue says the two paths share only `overlay_text.py`. That was true
when it was written. Since #683 and #693 the grid's overlay is built from
four modules that are **already single-shooter-ready and already have no
grid-specific assumptions**:

| module | what it gives you | grid-specific? |
|---|---|---|
| `overlay_layout.py` | `Anchor`, `Role`, `Emphasis`, `ColorToken`, `Element`, `Group`, `CellScale` | no -- pure declaration |
| `overlay_html.py` | `grid_html` (a whole canvas), `cell_html` (**one cell, standalone**) | no |
| `overlay_raster.py` | `Rasterizer` protocol + `ChromiumRasterizer` | no |
| `overlay_theme.py` | every colour token | no |

`overlay_html.cell_html` exists precisely for this: its docstring already
says *"valid to drop anywhere (a test, a future single-shooter port)
without also needing a whole document"*. And `overlay_html`'s `.cell`
rule already carries `TOP_LEFT`/`TOP_RIGHT`/`BOTTOM_LEFT`/`BOTTOM_RIGHT`
as `position: absolute` anchors, commented as *"the live overlay's own
corner anchors"*.

So the bridge is much shorter than the issue implies. The question is no
longer "how do we share any of this" but "does a single-shooter frame
want `grid_html` at 1x1 or `cell_html` on its own".

### Stale -- "the sprite machinery is per-cell, so a 1x1 grid is the natural bridge"

Still probably right, but verify it rather than assuming, and note that
`SpriteGeometry`'s `cell_width`/`cell_height` are floor division of canvas
by cols/rows. At 1x1 they are exactly the canvas, so the arithmetic is a
no-op -- but `CellScale.for_cell` would then resolve type sizes off the
**full frame height**, not off a cell. For a 1080p single-shooter export
that gives `live_primary = max(48, 1080 // 14) = 77px`, where the grid at
2x2 gives 48px. **That is a real output change** and #684's own hard
constraint is that single-shooter output must not move silently. Decide
deliberately whether the single-shooter overlay keeps today's sizes or
adopts the resolver's.

### Stale -- the cost argument, which now points the other way

The issue argues the step function is a win for the single-shooter path.
It is, and #693's measurements make the case **much stronger than the
issue could have known** -- because #693 also made each individual draw
4-5x more expensive, and the win survives that easily.

A 20s stage at 30fps with 30 shots:

| | draws | measured cost |
|---|---|---|
| today: per-frame PIL | 600 | ~36 s/stage (at ~60 ms/draw) |
| ported: event-stepped, Chromium | 31 | ~9 s/stage (at ~300 ms/sprite) |

**19x fewer draws, ~4x faster wall-clock even at browser prices.** Do not
repeat #693's framing that the browser is a cost to be justified; on this
path it is a straight win. Re-measure on the real fixture before quoting
these numbers -- they are arithmetic over #693's per-sprite figures, not
an end-to-end timing of this path.

## The one genuinely new problem: composition mechanism

This is where the two paths actually differ, and the issue does not
mention it.

- **The grid** writes one PNG per state and hands ffmpeg a **concat
  demuxer list** with per-entry durations (`overlay_sprites.write_concat_list`,
  plus `quantize_durations` snapping every boundary onto a whole output
  frame).
- **The single-shooter renderer** opens ffmpeg with `-f rawvideo` and
  writes `canvas.tobytes()` per frame to its stdin
  (`overlay_render.py:592-595`).

A stepped renderer has to either (a) adopt the concat-of-PNGs approach,
which brings `quantize_durations`' hard-won frame-boundary correctness
with it, or (b) keep the rawvideo pipe and write the *same* rasterized
buffer N times for the N frames a state spans. (b) is a smaller diff and
keeps the exact output contract (same fps, resolution, duration,
frame-for-frame with the trim -- the module docstring's first promise);
(a) shares more code but changes how the MOV is produced.

**Recommendation: start with (b).** The expensive thing is the *draw*,
not the pipe, and (b) cannot drift the timeline. Revisit (a) only if
something concrete wants it.

## What must not move

`tests/test_overlay_render.py` has **31 tests**. The issue is explicit
and it is the right rule:

> This must not change existing single-shooter output silently. Whatever
> lands needs a before/after comparison on real frames, and the existing
> overlay tests must keep passing unmodified -- an edit to one of them is
> a signal the output moved.

Treat an edit to any of those 31 as a finding to explain, not a chore.

## How to see what you are changing

`scripts/render_grid_frames.py` covers the *grid*. There is no equivalent
for the single-shooter export -- **building one is probably the first
task**, for the same reason #682 built the grid's: on this project every
overlay defect that mattered was found by rendering and measuring, none
by reading.

Two things make that cheap and reliable:

- **The render is bit-deterministic.** Verified during #693: rendering
  `main` twice through `render_grid_frames.py` produced byte-identical
  frames on every frame. So a before/after diff is meaningful -- but
  **always render the control** (`main` against `main`). During #693 a 7%
  pixel difference on a frame that draws nothing looked like a regression
  and was x264 reallocating bits across a GOP whose other frames changed.
  Max delta 40, zero pixels above 60: all sub-noise.
- **gaspode is headless.** Publish frames as an Artifact; local files do
  not reach the user.

## Baselines on `main` at `46b568a`

- Full suite: `2861 passed, 21 skipped`, `47 integration test(s) ran, 0 skipped`, ~145 s on CI.
- `src/splitsmith/overlay_render.py` -- 607 lines, `DefaultTemplate.draw_frame` at :238, the per-frame loop at :592.
- `src/splitsmith/compare/overlay_live.py` -- 303 lines, the pattern to copy.
- `src/splitsmith/compare/overlay_sprites.py` -- the state machine (`build_overlay_states`, `quantize_durations`, `write_concat_list`), no PIL.
- Callers of `render_overlay`: `cli.py:1176` and `ui/exports.py:406`. Both must keep working.

## Loose ends this port will trip over

- **`--max-height` / `--max-fps`** (`cli.py:1143`, `:1151`) exist to trade
  output quality for render time. #693's issue text already flagged that
  they *"sit badly beside #692's 4K must be crisp"*. A 19x cheaper
  renderer weakens their reason to exist. Do not delete them as a side
  effect -- but say plainly whether they still earn their place.
- **Scorecard access.** `compare/overlay_data.py` reads `StageEntry.scorecard`
  for the grid; the single-shooter path has no equivalent, which is why it
  cannot draw scoring. That is a data-plumbing task, separable from the
  rendering port. Consider landing the render port first.
- **`Template` ABC.** `overlay_render.py` ships one implementation and an
  abstract base kept for a hypothetical second template. Declared
  `Group`s make that ABC largely redundant. Reconcile, do not duplicate.
- **A browser becomes required for single-shooter overlays too.** #693
  made `--overlay` need Chromium for the grid and rewrote the degradation
  notice accordingly. The same decision has to be made here, and
  `cli.py` / `ui/exports.py` are the surfaces where a missing browser has
  to be reported.

## Sequencing

The issue asks for this after #682 and #683. Both are merged, and #693
(which the issue predates) is too -- so the port now targets a composed
structure with **one** text mechanism behind it, which is exactly what
#693 was sequenced first to achieve. Nothing blocks #684.

#692 (native 1080p / 2.7K / 4K) overlaps at one point: `CellScale`'s
absolute pixel floors doing double duty as a type scale. See the
`live_primary` note above -- if #692 lands first, this port inherits its
answer instead of inventing one.
