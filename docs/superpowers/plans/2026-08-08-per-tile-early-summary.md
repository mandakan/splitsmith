# Per-Tile Early Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the compare grid MP4, a tile that runs out of footage shows
its own stage-summary cell instead of going black, from the moment its
footage ends until the end-of-stage hold takes over.

**Architecture:** The stage summary is already composed as one
canvas-sized PNG per stage and joined after the action by `concat`. This
change opens that same PNG a second time as an extra ffmpeg input,
`crop`s one cell per present tile out of it, and `overlay`s each cell
onto the action stream with `enable='gte(t,<that tile's footage end>)'`.
The overlays sit after the clock `drawtext` chain and before the
`concat`, so a finished tile's held clock is replaced by the summary
exactly as it is during the hold.

**Tech Stack:** Python 3.11+, ffmpeg filter graphs, pytest (`-n auto` by
default; use `-n0` when iterating on one test), PIL + numpy for pixel
assertions.

**Spec:** `docs/superpowers/specs/2026-08-08-per-tile-early-summary-design.md`
-- read it before Task 1. It carries the decisions and the measurements
this plan implements.

## Global Constraints

- Python 3.11+, type hints everywhere, `pathlib.Path` never strings,
  f-strings, Black at line length 110, Ruff clean.
- `uv` for everything: `uv run pytest ...`, never bare `pytest`, never `pip`.
- No new dependencies.
- Detection/analysis logic stays out of `cli.py`. This change touches
  `src/splitsmith/compare/mp4_grid.py` only, on the source side.
- The feature is active only when `plan.hold_seconds > 0` **and** an
  overlay plan is present. A zero-hold render's argv must stay
  byte-identical; `test_zero_hold_produces_the_command_main_produces_today`
  and `test_no_hold_writes_no_still_and_changes_no_command` must pass
  **unedited**. If either needs a change, the gating is wrong.
- Filter labels introduced here are `still<N>`, `cell<N>`, `early<N>`.
  Do not use labels matching `[te]\d+` or `[am]\d+` -- existing tests
  select chains with those regexes.
- Never weaken an existing assertion to make it pass. Where behaviour
  genuinely inverts, invert the assertion and re-measure its threshold,
  keeping the old measurement in the comment.

---

### Task 1: Where a tile's footage ends

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py` (new module-level function near `_cell_size`, around line 671)
- Test: `tests/test_compare_mp4_grid_hold.py`

**Interfaces:**
- Consumes: `GridTile` (`mp4_grid.py:347`), fields `lead_pad_seconds`,
  `source_duration_seconds`, `seek_seconds`, `trim_path`.
- Produces: `mp4_grid.tile_footage_end_seconds(tile: GridTile) -> float`
  -- public (no leading underscore) so the tests can name it directly,
  the same way `audio_track_labels` is.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compare_mp4_grid_hold.py`, at the end of the file:

```python
# --- where a tile's own footage ends ----------------------------------------


def test_footage_end_is_the_head_pad_plus_this_tile_s_post_beep_span():
    """The two spellings of a tile's front collapse to the same answer.

    A tile either seeks into its clip (``seek_seconds > 0``, no lead pad)
    or cannot seek far enough back and gets a synthesised lead pad
    instead. Both put the beep on the head pad, so both must end at
    ``head_pad + (source - beep)`` -- and that is where the tile chain's
    black ``tpad`` starts.
    """
    # head_pad 1.0, beep 1.25 into a 6.0s clip: seeks 0.25, ends at 1.0+4.75.
    seeking = mp4_grid.GridTile(
        label="Bo",
        trim_path=Path("/trims/Bo.mov"),
        beep_offset_in_clip=1.25,
        seek_seconds=0.25,
        lead_pad_seconds=0.0,
        source_duration_seconds=6.0,
        row=0,
        col=1,
    )
    assert mp4_grid.tile_footage_end_seconds(seeking) == pytest.approx(5.75)

    # Same head pad, but the beep is only 0.4s in: the seek clamps at 0
    # and 0.6s of lead pad makes up the shortfall. Ends at 1.0 + 5.6.
    padded = mp4_grid.GridTile(
        label="Cy",
        trim_path=Path("/trims/Cy.mov"),
        beep_offset_in_clip=0.4,
        seek_seconds=0.0,
        lead_pad_seconds=0.6,
        source_duration_seconds=6.0,
        row=1,
        col=0,
    )
    assert mp4_grid.tile_footage_end_seconds(padded) == pytest.approx(6.6)


def test_footage_end_of_a_filler_tile_is_zero():
    """A filler has no source, so there is no footage to end.

    Callers must not paint a summary over it -- there is no shooter --
    and a filler that reported a positive end would arm one.
    """
    filler = mp4_grid.GridTile(
        label="Ann",
        trim_path=None,
        beep_offset_in_clip=0.0,
        seek_seconds=0.0,
        lead_pad_seconds=0.0,
        source_duration_seconds=0.0,
        row=0,
        col=0,
    )
    assert mp4_grid.tile_footage_end_seconds(filler) == 0.0


def test_footage_end_never_goes_negative():
    """A probe shorter than the seek is nonsense, not a negative time.

    ``source_duration_seconds`` comes off an ffprobe of the trim, so it
    can disagree with the seek by a rounding error rather than by a real
    quantity. Clamped here so no caller has to.
    """
    odd = mp4_grid.GridTile(
        label="Di",
        trim_path=Path("/trims/Di.mov"),
        beep_offset_in_clip=2.0,
        seek_seconds=1.0,
        lead_pad_seconds=0.0,
        source_duration_seconds=0.5,
        row=0,
        col=0,
    )
    assert mp4_grid.tile_footage_end_seconds(odd) == 0.0
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
uv run pytest tests/test_compare_mp4_grid_hold.py -n0 -k footage_end -v
```

Expected: 3 FAILED with `AttributeError: module 'splitsmith.compare.mp4_grid' has no attribute 'tile_footage_end_seconds'`.

- [ ] **Step 3: Implement it**

In `src/splitsmith/compare/mp4_grid.py`, immediately after `_cell_size`
(which ends around line 673) and before `_unreached_cells`:

```python
def tile_footage_end_seconds(tile: GridTile) -> float:
    """When this tile's own picture stops, in *segment* time.

    Not the end of the action. The stage runs ``head_pad + the longest
    tile's post-beep span + tail_pad`` and every tile chain is
    ``tpad``-ed with black across the remainder, so this is exactly
    where that black starts.

    Both spellings of a tile's front collapse to the same answer. A tile
    that could seek reads ``source - seek`` of picture with no lead pad;
    one that could not seek far enough back reads its whole clip behind
    ``lead_pad`` seconds of synthesised black. Either way the beep lands
    on the head pad and the picture ends a post-beep span later.

    ``0.0`` for a filler tile, which has no source at all -- see
    :attr:`GridTile.source_duration_seconds`. Clamped at zero rather
    than trusted: the duration is an ffprobe reading of the trim and can
    disagree with the seek by a rounding error, and a negative time
    would arm an ``enable`` expression from the first frame.
    """
    if tile.trim_path is None:
        return 0.0
    return max(0.0, tile.lead_pad_seconds + tile.source_duration_seconds - tile.seek_seconds)
```

- [ ] **Step 4: Run the tests and watch them pass**

```bash
uv run pytest tests/test_compare_mp4_grid_hold.py -n0 -k footage_end -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_hold.py
git commit -m "feat(compare): expose where a grid tile's own footage ends"
```

---

### Task 2: Let the video chain be composed instead of hard-wired

Pure refactor. `_clock_filters` currently closes the whole video half by
calling `_video_tail` itself, so there is nowhere to insert a filter
between the clock and the `concat`. After this task the graph is
byte-identical and there is a seam.

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py:823-937` (`_clock_filters`), `:1313-1329` (`_build_filter_graph`'s video tail)
- Test: `tests/test_compare_mp4_grid_hold.py`, `tests/test_compare_mp4_grid_overlay.py` (existing, must not change)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `_clock_filters(plan, canvas, overlay) -> tuple[list[str], str]`
  -- the filter chains and the label the video half currently ends on
  (`"ovlgrid"` when there are no clocks, `"ovltext"` when there are).
  The `hold_label` keyword argument is **removed**; `_video_tail` is
  called by `_build_filter_graph` instead.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_compare_mp4_grid_hold.py`:

```python
def test_clock_filters_hand_back_the_label_the_action_ends_on(tmp_path: Path):
    """The seam a per-tile summary needs, pinned as its own contract.

    ``_clock_filters`` used to close the video half itself. It now
    returns what it drew and where the action's stream is, so a caller
    can put something between it and the ``concat`` -- which is the only
    place a filter may go if it is to reach the action and not the hold.
    """
    canvas = mp4_grid.GridCanvas(1920, 1080, 25, 1)
    clocks = (
        mp4_grid.TileClock(row=0, col=0, start_seconds=1.0, freeze_seconds=6.0, final_text="5.00"),
    )

    with_clock, label = mp4_grid._clock_filters(
        _plan(hold=HOLD), canvas, _overlay_plan(tmp_path, clocks=clocks)
    )
    assert label == "ovltext"
    assert len(with_clock) == 1
    assert with_clock[0].startswith("[ovlgrid]drawtext=")
    assert with_clock[0].endswith("[ovltext]")

    # No clocks at all: nothing is drawn and the action is still [ovlgrid].
    without, bare_label = mp4_grid._clock_filters(_plan(hold=HOLD), canvas, _overlay_plan(tmp_path))
    assert without == []
    assert bare_label == "ovlgrid"
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run pytest tests/test_compare_mp4_grid_hold.py -n0 -k clock_filters_hand_back -v
```

Expected: FAIL -- `_clock_filters` returns a `list`, so unpacking it into
`(with_clock, label)` raises `ValueError: too many values to unpack` (or
`TypeError`, depending on how many filters the plan produces).

- [ ] **Step 3: Change `_clock_filters` to return its label**

In `src/splitsmith/compare/mp4_grid.py`, change the signature at line 823:

```python
def _clock_filters(
    plan: GridStagePlan,
    canvas: GridCanvas,
    overlay: StageOverlayPlan,
) -> tuple[list[str], str]:
```

and the last three lines of its body (currently lines 935-937):

```python
    if not filters:
        return [], "ovlgrid"
    return ["[ovlgrid]" + ",".join(filters) + "[ovltext]"], "ovltext"
```

In the docstring, keep every measured paragraph verbatim. Replace only
the summary line and the `hold_label` mention: the function now returns
`(filters, label)` and no longer closes the graph. The paragraph
beginning "**Two of these windows are open-ended above, and that is
correct.**" stays exactly as it is -- its claim (these filters hang off
the action, which `concat` joins the hold after) is still true, and it
names the test that guards it.

- [ ] **Step 4: Rewire `_build_filter_graph`**

Replace lines 1313-1329 (`if overlay is None:` through the
`parts.extend(_clock_filters(...))` line) with:

```python
    if overlay is None:
        video_label = "grid"
    else:
        if sprite_index is None:
            raise ValueError("an overlay plan needs the input index its sprite sequence was added at")
        # ``stop_mode=clone`` holds the last state's alpha; the default
        # ``add`` would pad with opaque black and paint the grid out at
        # the end. The explicit ``trim`` means the segment's length never
        # depends on ``overlay``'s ``eof_action`` default, which is what
        # the concat stitch's uniform-stream rule ultimately rests on.
        parts.append(
            f"[{sprite_index}:v]format=rgba,fps={rate},setpts=PTS-STARTPTS,"
            f"tpad=stop_duration={plan.duration_seconds:g}:stop_mode=clone,"
            f"trim=0:{plan.duration_seconds:g}[ovl]"
        )
        parts.append("[grid][ovl]overlay=0:0:format=auto[ovlgrid]")
        clock_parts, video_label = _clock_filters(plan, canvas, overlay)
        parts.extend(clock_parts)

    parts.extend(_video_tail(video_label, hold_label))
```

Note the audio chains follow this in the function and are untouched.

- [ ] **Step 5: Run the whole grid suite and confirm nothing moved**

```bash
uv run pytest tests/test_compare_mp4_grid_hold.py tests/test_compare_mp4_grid_overlay.py tests/test_compare_mp4_grid_commands.py -n0 -q
```

Expected: all PASS, with no test file edited. This is the point of the
task: the argv is byte-identical, so every existing assertion about it
still holds.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_hold.py
git commit -m "refactor(compare): let the grid's video chain be composed, not hard-wired"
```

---

### Task 3: Paint each tile's summary cell from its own footage end

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py` -- new `_early_summary_filters` (place it directly after `_clock_filters`), `build_stage_command`'s input block (after line 1107), `_build_filter_graph`'s signature and video tail
- Test: `tests/test_compare_mp4_grid_hold.py`

**Interfaces:**
- Consumes: `tile_footage_end_seconds` (Task 1), `_clock_filters`
  returning `(filters, label)` (Task 2).
- Produces:
  `_early_summary_filters(plan: GridStagePlan, canvas: GridCanvas, source_label: str, early_index: int) -> tuple[list[str], str]`,
  and `_build_filter_graph(..., early_index: int | None = None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compare_mp4_grid_hold.py`:

```python
# --- the per-tile early summary ---------------------------------------------
#
# The plan `_plan()` builds has three tiles: Ann is filler (row0,col0),
# Bo is present at row0,col1 with no lead pad, Cy is present at row1,col0
# with a 0.5s lead pad. Every present tile has a 6.0s source read from a
# 0.25s seek, so on a 1920x1080 / 25fps canvas:
#   Bo ends at 0.0 + 6.0 - 0.25 = 5.75, arms one frame earlier at 5.71
#   Cy ends at 0.5 + 6.0 - 0.25 = 6.25, arms at 6.21
# The cells are 960x540 and the fourth is unreached.
BO_ARM = "5.71"
CY_ARM = "6.21"


def test_each_present_tile_gets_its_summary_cell_at_its_own_footage_end(tmp_path: Path):
    graph = _graph_of(_command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path)))

    # Bo is at row0,col1 -- crop and paste at x=960, y=0.
    assert "[still0]crop=960:540:960:0[cell0]" in graph, graph
    assert rf"[cell0]overlay=960:0:format=auto:enable='gte(t\,{BO_ARM})'" in graph, graph
    # Cy is at row1,col0 -- x=0, y=540, and arms later because of her lead pad.
    assert "[still1]crop=960:540:0:540[cell1]" in graph, graph
    assert rf"[cell1]overlay=0:540:format=auto:enable='gte(t\,{CY_ARM})'" in graph, graph


def test_the_filler_tile_and_the_unreached_cell_get_no_summary(tmp_path: Path):
    """Neither is a shooter. Text or a picture over either invents one."""
    graph = _graph_of(_command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path)))

    # Two present tiles, so two crops, two overlays and a two-way split.
    assert graph.count("crop=960:540") == 2, graph
    assert "split=2[still0][still1]" in graph, graph
    assert "[still2]" not in graph, graph
    # Ann's cell (row0,col0) and the unreached cell (row1,col1) are never
    # a paste target.
    assert "overlay=0:0:format=auto:enable=" not in graph, graph
    assert "overlay=960:540" not in graph, graph


def test_the_early_summary_is_composited_after_the_clock_and_before_the_join(tmp_path: Path):
    """Order is the whole design, and both halves of it are load-bearing.

    *After* the clock: a finished tile's held final time is a ``drawtext``
    with no upper bound, so composited the other way round it would sit
    on top of the summary cell -- which the hold itself never shows --
    and vanish at the cut to the hold.

    *Before* the join: these overlays must reach the action and nothing
    else. ``_video_tail``'s guarantee is structural, not an expression,
    and it only holds while every compositing filter is upstream of the
    ``concat``.
    """
    clocks = (mp4_grid.TileClock(row=0, col=1, start_seconds=1.0, freeze_seconds=4.0, final_text="3.00"),)
    graph = _graph_of(
        _command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path, clocks=clocks))
    )
    chains = graph.split(";")

    drawn = next(index for index, part in enumerate(chains) if "drawtext=" in part)
    first_early = next(index for index, part in enumerate(chains) if "[cell0]overlay=" in part)
    joined = next(index for index, part in enumerate(chains) if "concat=n=2" in part)

    assert drawn < first_early < joined, chains
    # The clock's own output is what the first early overlay draws onto.
    assert chains[first_early].startswith("[ovltext][cell0]"), chains[first_early]
    # And the join takes the last early label, so nothing skipped it.
    assert chains[joined].startswith("[early1][hold]"), chains[joined]

    # With --no-clock there is no drawtext at all, so the first early
    # overlay draws straight onto the sprite composite instead. Same
    # position, one less link.
    bare = _graph_of(_command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path))).split(";")
    assert not any("drawtext=" in part for part in bare), bare
    first_bare = next(index for index, part in enumerate(bare) if "[cell0]overlay=" in part)
    assert bare[first_bare].startswith("[ovlgrid][cell0]"), bare[first_bare]


def test_the_early_still_is_a_second_input_after_the_hold_still(tmp_path: Path):
    """Appended last, and read for the action rather than the hold.

    Two inputs on the same PNG rather than one ``split``: each input's
    ``-t`` then means exactly one thing, and the hold chain -- whose
    length is the only thing standing between this segment and an audio
    stream that outlasts its video -- is left untouched.
    """
    cmd = _command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path))
    inputs = [value for flag, value in zip(cmd, cmd[1:], strict=False) if flag == "-i"]

    assert inputs[-1] == str(HOLD_STILL)
    assert inputs[-2] == str(HOLD_STILL)
    assert inputs[-3] == str(tmp_path / "sprites.txt")
    # The hold reads the hold's worth; the early summary reads the action.
    assert _input_durations(cmd)[-2:] == [f"{HOLD:g}", f"{ACTION:g}"]
    # And the graph reads each at the index its placement implies.
    assert f"[{len(inputs) - 2}:v]" in _graph_of(cmd)  # the hold
    assert f"[{len(inputs) - 1}:v]" in _graph_of(cmd)  # the early summary


def test_no_early_summary_without_a_hold_or_without_an_overlay(tmp_path: Path):
    """Both halves of the gate, because each fails silently on its own.

    With no hold there is no still to crop -- ``render_grid_mp4`` only
    composes one when a hold was asked for -- and an ``--overlay``-only
    render must come out byte-identical to what shipped before this.
    With no overlay the shape is one ``build_stage_command`` accepts but
    ``render_grid_mp4`` refuses outright, so it has no pixel coverage and
    must not grow behaviour.
    """
    for overlay in (None, _overlay_plan(tmp_path)):
        for hold in (None, 0.0):
            graph = _graph_of(_command(_plan(hold=hold), overlay=overlay))
            assert "crop=960:540" not in graph, (hold, overlay)

    no_overlay = _graph_of(_command(_plan(hold=HOLD), overlay=None))
    assert "crop=960:540" not in no_overlay
    assert "[grid][hold]concat=n=2:v=1:a=0[joined]" in no_overlay


def test_a_tile_whose_footage_covers_the_whole_action_gets_a_cell_that_never_arms(
    tmp_path: Path,
):
    """It is emitted anyway, and that is the correct render.

    A tile long enough to fill the action has no black to cover, so its
    ``enable`` simply never becomes true and ffmpeg draws nothing.
    Emitted unconditionally rather than filtered out: skipping it would
    make the filter *count* depend on clip lengths, which is a much
    easier thing to get wrong -- and to fail to notice -- than an
    expression that is never satisfied.
    """
    base = _plan(hold=HOLD)
    # Bo reads a clip longer than the whole action: 20.0s against 12.5s.
    tiles = tuple(
        dataclasses.replace(tile, source_duration_seconds=20.0) if tile.label == "Bo" else tile
        for tile in base.tiles
    )
    graph = _graph_of(
        _command(dataclasses.replace(base, tiles=tiles), overlay=_overlay_plan(tmp_path))
    )
    arms = [float(value) for value in re.findall(r"enable='gte\(t\\,([\d.]+)\)'", graph)]

    assert len(arms) == 2, graph
    # Bo's arm is past the end of the action, so it never fires; Cy's is
    # inside it and does.
    assert max(arms) > base.duration_seconds
    assert min(arms) < base.duration_seconds
```

- [ ] **Step 2: Run them and watch them fail**

```bash
uv run pytest tests/test_compare_mp4_grid_hold.py -n0 -k "early_summary or summary_cell or early_still or filler_tile_and_the_unreached" -v
```

Expected: FAIL. `test_no_early_summary_without_a_hold_or_without_an_overlay`
passes already (nothing emits a crop yet) -- that is fine and expected;
every other new test fails on a missing `crop=960:540`.

- [ ] **Step 3: Write the filter builder**

In `src/splitsmith/compare/mp4_grid.py`, directly after `_clock_filters`:

```python
def _early_summary_filters(
    plan: GridStagePlan,
    canvas: GridCanvas,
    source_label: str,
    early_index: int,
) -> tuple[list[str], str]:
    """Paint each present tile's summary cell from its own footage end.

    A tile's chain is ``tpad``-ed with black from where its own clip runs
    out to the end of the action (see :func:`tile_footage_end_seconds`),
    so a shooter who finished first sat on a black cell until the last
    tile was done. This paints that tile's cell of the stage summary
    over the black instead, leaving the end-of-stage hold to take over
    at ``duration_seconds`` with pixel-identical content -- the cut is
    invisible because both come from the same PNG.

    Cropping the composed still is exact rather than approximate.
    ``overlay_html.grid_html`` gives every cell ``overflow: hidden`` and
    builds its content from that label's own ``TileStageData``, so no
    element crosses a cell boundary and no cell depends on another
    shooter. A crop of the still is therefore the same pixels that cell
    will show during the hold.

    **One frame early**, and the direction matters. Arming late by a
    frame shows a black frame, which is the whole defect; arming early
    covers the tile's last footage frame with a blurred, dimmed copy of
    itself, which nothing can see. ``source_duration_seconds`` is an
    ffprobe reading, so disagreeing with the decoded stream by a fraction
    of a frame is the expected case rather than the exceptional one.

    Filler tiles get nothing: an empty cell is not a shooter, and
    ``build_hold_still`` draws no summary into one either.

    Returns the filters and the label the video half now ends on. The
    caller must keep these upstream of :func:`_video_tail`'s ``concat``
    -- that is what keeps every compositing filter on the action, which
    is the structural bound the hold's correctness rests on.
    """
    present = [tile for tile in plan.tiles if tile.trim_path is not None]
    if not present:
        return [], source_label

    cell_w, cell_h = _cell_size(canvas, plan)
    frame_seconds = 1.0 / canvas.fps
    branches = "".join(f"[still{index}]" for index in range(len(present)))
    # ``split=1`` is legal but reads as a mistake; ``null`` is the same
    # graph with one output. The scale/setsar/fps conform mirrors the
    # hold chain: it is a no-op on a still this module composed and the
    # guard against one it did not.
    fan_out = f"split={len(present)}" if len(present) > 1 else "null"
    filters = [
        f"[{early_index}:v]setpts=PTS-STARTPTS,scale={canvas.width}:{canvas.height},"
        f"setsar=1,fps={canvas.rate_string},{fan_out}{branches}"
    ]

    label = source_label
    for index, tile in enumerate(present):
        left = tile.col * cell_w
        top = tile.row * cell_h
        arm = max(0.0, tile_footage_end_seconds(tile) - frame_seconds)
        filters.append(f"[still{index}]crop={cell_w}:{cell_h}:{left}:{top}[cell{index}]")
        filters.append(
            f"[{label}][cell{index}]overlay={left}:{top}:format=auto:"
            f"enable='gte(t\\,{arm:g})'[early{index}]"
        )
        label = f"early{index}"
    return filters, label
```

- [ ] **Step 4: Add the input and wire the filters**

In `build_stage_command`, after the `hold_index` block that ends at line
1107 (`next_index += 1`), add:

```python
    # After the hold's own input, for the same reason the hold went after
    # the sprite: the only index safe to occupy is the next free one.
    # The same PNG, opened a second time and read for the *action* -- see
    # ``_early_summary_filters``. A second input rather than a ``split``
    # off the hold's so each ``-t`` states one length, and so the hold
    # chain, whose length is all that stands between this segment and an
    # audio stream outlasting its video, is left exactly as it was.
    #
    # Gated on the overlay too, not just the hold. A hold with no overlay
    # is a shape ``render_grid_mp4`` refuses outright, so it reaches no
    # pixel test and must not grow behaviour here.
    early_index: int | None = None
    if hold_index is not None and overlay is not None:
        args += [
            "-loop",
            "1",
            "-framerate",
            rate,
            "-t",
            f"{plan.duration_seconds:g}",
            "-i",
            str(hold_still_path),
        ]
        early_index = next_index
        next_index += 1
```

Pass it through in the `_filter_complex` call just below:

```python
            hold_index=hold_index,
            early_index=early_index,
```

Add the parameter to `_build_filter_graph`'s signature:

```python
    hold_index: int | None = None,
    early_index: int | None = None,
```

and insert the filters in its video tail, between the clock and
`_video_tail` (the block Task 2 created):

```python
        clock_parts, video_label = _clock_filters(plan, canvas, overlay)
        parts.extend(clock_parts)

    if early_index is not None:
        early_parts, video_label = _early_summary_filters(plan, canvas, video_label, early_index)
        parts.extend(early_parts)

    parts.extend(_video_tail(video_label, hold_label))
```

Finally, add a paragraph to `_build_filter_graph`'s docstring after the
one about the overlay being composited onto `[grid]`:

```
    ``early_index`` names a second read of the hold still, cropped per
    tile and composited onto the action from each tile's own footage end
    (:func:`_early_summary_filters`). It is composited after the clock
    and before the ``concat``, which is the only position that both
    replaces a finished tile's held clock and stays on the action.
```

- [ ] **Step 5: Run the new tests**

```bash
uv run pytest tests/test_compare_mp4_grid_hold.py -n0 -k "early_summary or summary_cell or early_still or filler_tile_and_the_unreached or covers_the_whole_action" -v
```

Expected: all PASS.

- [ ] **Step 6: Run the whole hold + overlay + commands suite and triage**

```bash
uv run pytest tests/test_compare_mp4_grid_hold.py tests/test_compare_mp4_grid_overlay.py tests/test_compare_mp4_grid_commands.py tests/test_compare_mp4_grid_plan.py -n0 -q
```

Expected: exactly two failures, both in `test_compare_mp4_grid_hold.py`:
`test_hold_still_input_is_appended_after_the_sprite_input` and
`test_the_hold_does_not_touch_the_clock_windows`.
(`test_the_sprite_overlay_does_not_reach_the_hold` already accepts either
`[ovlgrid][hold]` or `[ovltext][hold]` -- if it also fails, that is the
third expected one, fixed in the next step.)

**If anything else fails, stop and read it.** In particular
`test_zero_hold_produces_the_command_main_produces_today` and
`test_no_hold_writes_no_still_and_changes_no_command` failing means the
gate is wrong, not that they need editing.

- [ ] **Step 7: Extend the three tests that assert the old tail shape**

In `tests/test_compare_mp4_grid_hold.py`:

`test_hold_still_input_is_appended_after_the_sprite_input` -- replace the
assertion block, keeping the docstring comment above it intact:

```python
    cmd = _command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path))
    inputs = [value for flag, value in zip(cmd, cmd[1:], strict=False) if flag == "-i"]

    # The still twice -- the hold's read and the early summary's -- then
    # the sprite before both. Nothing is inserted ahead of a tile.
    assert inputs[-1] == str(HOLD_STILL)
    assert inputs[-2] == str(HOLD_STILL)
    assert inputs[-3] == str(tmp_path / "sprites.txt")
    # And the graph reads each at the index that placement implies.
    assert f"[{len(inputs) - 1}:v]" in _graph_of(cmd)
    assert f"[{len(inputs) - 2}:v]" in _graph_of(cmd)
    assert f"[{len(inputs) - 3}:v]format=rgba" in _graph_of(cmd)
```

`test_the_sprite_overlay_does_not_reach_the_hold` -- replace the last
assertion (`assert join.startswith(...)`) with:

```python
    # The join's first input is whatever the action chain ends on -- the
    # composite, the clock, or the last early summary cell. Whichever it
    # is, the sprite is upstream of it, so it cannot reach a hold frame.
    assert join.split("][")[0].lstrip("[") in {"ovlgrid", "ovltext", "early0", "early1"}, join
    assert join.endswith("[hold]concat=n=2:v=1:a=0[joined]"), join
```

`test_the_hold_does_not_touch_the_clock_windows` -- narrow `_drawtext`'s
predicate and say why:

```python
    def _drawtext(hold: float) -> list[str]:
        graph = _graph_of(_command(_plan(hold=hold), overlay=_overlay_plan(tmp_path, clocks=clocks)))
        # ``drawtext=`` alone, not "anything with an enable": the per-tile
        # early summary is an ``overlay`` with its own ``enable``, and it
        # is *supposed* to appear only under a hold. The claim here is
        # about the clock windows, and the ``lt(t\,`` count below is what
        # holds the line that nothing else introduced an upper bound.
        return [part for part in graph.split(",") if "drawtext=" in part]
```

- [ ] **Step 8: Prove the fix is what makes the new tests pass**

Delete the two-line body of the `if early_index is not None:` block in
`_build_filter_graph` (leave `pass`), re-run, and confirm the new tests
fail. Then restore it.

```bash
uv run pytest tests/test_compare_mp4_grid_hold.py -n0 -q
```

Expected with the block disabled: the Task 3 tests FAIL. Expected with it
restored: the whole file PASSES.

- [ ] **Step 9: Format, lint, commit**

```bash
uv run black src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_hold.py
uv run ruff check src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_hold.py
uv run pytest tests/test_compare_mp4_grid_hold.py tests/test_compare_mp4_grid_overlay.py tests/test_compare_mp4_grid_commands.py -n0 -q
git add src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_hold.py
git commit -m "feat(compare): show each tile's stage summary from its own footage end"
```

---

### Task 4: Prove it in the pixels

The argv tests above cannot tell you whether any of this reached a frame.
This task is the instrument, and it is also where two existing
assertions -- which assert *black* exactly where the summary now goes --
get inverted.

**Files:**
- Modify: `tests/test_compare_grid_overlay_integration.py` (constants block around lines 290-395; `test_the_summary_hold_reaches_the_rendered_pixels`, lines 1145-1284)

**Interfaces:**
- Consumes: the rendered output of Task 3.
- Produces: nothing other tasks depend on.

**Read first:** the module docstring (lines 1-68) and the comment block
above the thresholds (lines 155-182). Every number in that file was
measured against this fixture and is documented with what it measured
and what the failing baseline read. New numbers go in the same way.
Never copy a threshold from this plan without re-measuring it.

- [ ] **Step 1: Add the two new frame indices**

In the constants block, after `PICTURE_INDEX` and its assertion (line
314-320), add:

```python
#: The frame the short tile's summary arms on, and one safely before it.
#:
#: Mathias's clip runs out at :data:`SHORT_FOOTAGE_ENDS` (5.4985s, frame
#: 165) and the summary arms one frame earlier, so frames from 164 on
#: carry it. BEFORE_ARM_INDEX keeps five frames of clearance below that
#: -- the same margin :data:`PICTURE_INDEX` keeps below its own cliff,
#: and for the same reason: derived from the geometry so a change to the
#: pads or the clip lengths moves the sample instead of silently moving
#: the boundary underneath a literal.
SHORT_ARM_INDEX = round(SHORT_FOOTAGE_ENDS * 30)
BEFORE_ARM_INDEX = SHORT_ARM_INDEX - 5

# Both clocked shooters must still have live picture at BEFORE_ARM_INDEX
# -- it is where the clock check samples, and a clock corner covered by a
# summary proves nothing about a clock. Anders and Bea run to 7.0s;
# Mathias is the binding one.
assert BEFORE_ARM_INDEX > round(HEAD_PAD_SECONDS * 30)
assert BEFORE_ARM_INDEX < SHORT_ARM_INDEX - 1
```

- [ ] **Step 2: Replace the two inverted thresholds**

Delete `TAIL_PAD_MIN_BLACK_FRACTION` (line 370-375) and
`SHORT_TILE_MIN_BLACK_FRACTION` (line 377-381) with their docstrings, and
put this in their place. Leave the measured values as `0.0` for now --
Step 6 fills them in from a real render.

```python
#: A tile's cell during the action against the same cell of the still the
#: render composed for that stage.
#:
#: This is the early summary's instrument, and it replaces
#: ``SHORT_TILE_MIN_BLACK_FRACTION``, which asserted the opposite: that
#: Mathias's cell was 80.4% pure black at :data:`PICTURE_INDEX` because
#: his shorter clip had run out and the tile chain was ``tpad``-ed black
#: to the end of the action. It now carries his summary from his own
#: footage end, so the same crop is the still's own cell.
#:
#: Measured on this fixture: TODO in the cell at PICTURE_INDEX (the
#: summary), TODO in the same cell at :data:`BEFORE_ARM_INDEX` (live
#: footage against a blurred still).
EARLY_SUMMARY_MATCHES_STILL_MAX = 0.0

#: The same crop before the summary arms, which must still be footage.
#:
#: Without this, an ``enable`` expression that is simply always true --
#: the summary painted over the whole action from frame zero -- passes
#: every other check in this test.
BEFORE_ARM_MIN_DIFF_TO_STILL = 0.0

#: The whole canvas on the action's last frame against the composed
#: still.
#:
#: Replaces ``TAIL_PAD_MIN_BLACK_FRACTION``, which asserted that the last
#: action frame was 100.0% black in a picture crop because it sits inside
#: the tail pad. Every present tile is showing its summary by then now,
#: so the frame *is* the still -- the same measure
#: :data:`HOLD_MATCHES_ITS_STILL_MAX` applies inside the hold, which is
#: the point: the cut from the action to the hold is invisible.
#:
#: Measured: TODO here, against TODO on the same frame before this
#: change (raw tail-pad black against a composed summary).
LAST_ACTION_MATCHES_STILL_MAX = 0.0
```

- [ ] **Step 3: Move what the new assertions need above them**

The new assertions compare frames against the composed still, and the
still is opened at line 1207 -- *below* the block being replaced. Move
three things up, in this order, to sit immediately after the frame
decodes at line 1147:

1. `whole = (0, 0, CANVAS.width, CANVAS.height)` (currently line 1209),
   up beside the cell boxes around line 1143.
2. The "--- one freeze frame per present tile, per stage ---" block
   (lines 1195-1204), which defines `work_dir`. Keep its comment: it
   still says which layer failed when the pixel checks go red, and that
   is now true of the early-summary checks too.
3. `still = Image.open(work_dir / "summary-stage1.png").convert("RGB")`
   and `assert still.size == (CANVAS.width, CANVAS.height)` (lines
   1207-1208).

Leave the "THE instrument" assertion itself (`to_still = ...`) where it
is, under its own heading.

Then add the new frame beside the existing two:

```python
    before_arm = _frame_at_index(held, BEFORE_ARM_INDEX, tmp_path, "held-before-arm")
```

- [ ] **Step 4: Rewrite the three assertion blocks**

Replace the whole "--- and it cannot have come from the end of the
action ---" block (lines 1175-1193) with:

```python
    # --- each tile's summary starts at that tile's own footage end -------
    #
    # Mathias's clip is shorter, so at PICTURE_INDEX he has run out while
    # Anders still has picture. His cell must be the still's own cell by
    # then -- not black, which is what it was before the early summary,
    # and not live footage.
    early = _mean_abs_diff(with_picture, still, mathias_cell)
    assert early <= EARLY_SUMMARY_MATCHES_STILL_MAX, (
        f"the short tile is not showing its summary at frame {PICTURE_INDEX}: mean abs diff "
        f"{early:.2f} against the same cell of the composed still (threshold "
        f"{EARLY_SUMMARY_MATCHES_STILL_MAX}). A cell that reads near "
        f"{_mean_abs_diff(before_arm, still, mathias_cell):.2f} is still live footage; one that "
        "is mostly black is the tpad the early summary exists to cover."
    )
    assert _black_fraction(with_picture, mathias_cell) <= HOLD_CELL_MAX_BLACK_FRACTION
    # And Anders, whose clip has not run out, is still live footage.
    assert _black_fraction(with_picture, anders_cell) <= HOLD_CELL_MAX_BLACK_FRACTION

    # --- and not one frame before it -------------------------------------
    # The check that fails against an ``enable`` that is always true,
    # which every other assertion here would pass.
    not_yet = _mean_abs_diff(before_arm, still, mathias_cell)
    assert not_yet >= BEFORE_ARM_MIN_DIFF_TO_STILL, (
        f"the short tile is already showing its summary at frame {BEFORE_ARM_INDEX}, "
        f"{SHORT_ARM_INDEX - BEFORE_ARM_INDEX} frames before its footage runs out: "
        f"{not_yet:.2f} against the still (threshold {BEFORE_ARM_MIN_DIFF_TO_STILL})"
    )

    # --- an empty cell is still empty -------------------------------------
    assert _black_fraction(with_picture, unreached_cell) >= EMPTY_CELL_MIN_BLACK_FRACTION, (
        "the early summary drew into the unreached cell -- an empty cell is not a shooter"
    )

    # --- by the last action frame every tile has switched ------------------
    # So the cut to the hold is invisible: the action's final frame and
    # the hold's frames are the same composed still. This replaces the
    # tail-pad-is-black assertion, which measured the defect.
    last_vs_still = _mean_abs_diff(last_action, still, whole)
    assert last_vs_still <= LAST_ACTION_MATCHES_STILL_MAX, (
        f"the action's last frame is not the composed summary: mean abs diff {last_vs_still:.2f} "
        f"over the whole canvas (threshold {LAST_ACTION_MATCHES_STILL_MAX}). Every tile's footage "
        "has ended by then, so every cell should already be carrying its own summary."
    )
```

Note `whole` is defined at line 1209, after this block. Move that line
(`whole = (0, 0, CANVAS.width, CANVAS.height)`) up to sit beside the cell
boxes around line 1143.

Then fix the now-misleading error message in the "THE instrument" block
(line 1214-1216): the last action frame no longer reads as raw footage,
so it is no longer a useful contrast. Replace those three lines with:

```python
        f"the hold is not showing the still this render composed for it: mean abs diff "
        f"{to_still:.2f} over the whole canvas (threshold {HOLD_MATCHES_ITS_STILL_MAX}). "
        f"The same measure on a frame before any tile finished is "
        f"{_mean_abs_diff(before_arm, still, whole):.2f} -- if this reads near that, the "
        "video half never got the still and the segment is holding raw footage."
```

Finally, move the clock check's sample frame. At line 1262, replace:

```python
        action_clock = _crop_diff(last_action, clock_box, bea_clock)
```

with:

```python
        # Sampled before any tile's summary has armed, not on the last
        # action frame: by then every present cell is carrying its own
        # summary and this corner would be comparing two summaries rather
        # than a clock against a shooter who never gets one.
        action_clock = _crop_diff(before_arm, clock_box, bea_clock)
```

- [ ] **Step 5: Run it and watch it fail on the placeholder thresholds**

```bash
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest \
  tests/test_compare_grid_overlay_integration.py::test_the_summary_hold_reaches_the_rendered_pixels \
  -n0 -m integration -v
```

Expected: FAIL on `EARLY_SUMMARY_MATCHES_STILL_MAX = 0.0`. The assertion
message prints the measured value -- that is the number Step 6 needs.

- [ ] **Step 6: Measure the three thresholds and pin them**

Run this against the render the test just left behind. It prints every
number the constants need, plus the contrast each one has to sit between.

```bash
uv run python - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from PIL import Image

from tests.test_compare_grid_overlay_integration import (
    BEFORE_ARM_INDEX, PICTURE_INDEX, LAST_ACTION_INDEX, MID_HOLD_INDEX, CANVAS,
    _frame_at_index, _mean_abs_diff, _black_fraction,
)
# Point this at the work dir the failing run wrote (pytest prints its tmp_path).
work = Path(sys.argv[1] if len(sys.argv) > 1 else input("tmp_path from the failing run: ").strip())
held = work / "held.mp4"
still = Image.open(work / "work-held.mp4" / "summary-stage1.png").convert("RGB")
cw, ch = CANVAS.width // 2, CANVAS.height // 2
mathias = (0, ch, cw, 2 * ch)
whole = (0, 0, CANVAS.width, CANVAS.height)
out = work / "measure"
out.mkdir(exist_ok=True)
for name, index in (
    ("before-arm", BEFORE_ARM_INDEX), ("picture", PICTURE_INDEX),
    ("last-action", LAST_ACTION_INDEX), ("mid-hold", MID_HOLD_INDEX),
):
    frame = _frame_at_index(held, index, out, name)
    print(f"{name:12} frame {index:4}  mathias-cell vs still {_mean_abs_diff(frame, still, mathias):7.2f}"
          f"  whole vs still {_mean_abs_diff(frame, still, whole):7.2f}"
          f"  mathias black {_black_fraction(frame, mathias):6.3f}")
PY
```

Set each constant with a band between the two readings it separates, in
the style the file already uses (the existing ones sit roughly 2-4x clear
of both sides), and replace every `TODO` in the docstrings with the
number actually measured. Record the `before-arm` reading in
`EARLY_SUMMARY_MATCHES_STILL_MAX`'s docstring as the contrast, and the
`mid-hold` reading as the confirmation that the early cell and the held
cell are the same pixels.

- [ ] **Step 7: Run the test green**

```bash
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest \
  tests/test_compare_grid_overlay_integration.py -n0 -m integration -q
```

Expected: PASS, all tests in the module.

- [ ] **Step 8: Prove the new assertions can fail -- the mutation drill**

This is the step the whole task exists for. A test that would have passed
against the bug is not evidence.

```bash
git stash push src/splitsmith/compare/mp4_grid.py
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest \
  tests/test_compare_grid_overlay_integration.py::test_the_summary_hold_reaches_the_rendered_pixels \
  -n0 -m integration -q
git stash pop
```

Expected: FAIL, and specifically on
`EARLY_SUMMARY_MATCHES_STILL_MAX` (the short tile's cell is black, so the
diff against the still is large). Record the failing number in that
constant's docstring as the baseline -- that is what the module's other
constants all do.

Then the always-armed mutation, which is the one the "before arm" check
exists for. Temporarily change the `arm` line in `_early_summary_filters`
to `arm = 0.0`, re-run, and confirm `BEFORE_ARM_MIN_DIFF_TO_STILL` fails.
Restore it.

**If either mutation leaves the test green, the assertion is not an
instrument. Fix the assertion before continuing.**

- [ ] **Step 9: Commit**

```bash
uv run black tests/test_compare_grid_overlay_integration.py
uv run ruff check tests/test_compare_grid_overlay_integration.py
git add tests/test_compare_grid_overlay_integration.py
git commit -m "test(compare): prove each tile's summary starts at its own footage end"
```

---

### Task 5: Keep the design-review script honest

`scripts/render_grid_frames.py` is the instrument a design pass looks at,
and two of its captions now describe the defect rather than the render.

**Files:**
- Modify: `scripts/render_grid_frames.py:143-163`

**Interfaces:**
- Consumes: nothing. Independent of Tasks 1-4 except that it renders them.
- Produces: nothing.

- [ ] **Step 1: Fix the two captions**

The `short-tile-black` moment and the `last-action` moment both assert
black in their names or captions. With a hold they now show summaries;
without one they still show black. Replace both `Moment(...)` entries
(lines 148-163) with:

```python
        Moment(
            "short-tile-ends",
            at((SHORT_FOOTAGE_ENDS + HEAD_PAD_SECONDS + POST_BEEP_SECONDS) / 2),
            "the short clip has run out while the others still have picture"
            + (
                " -- with a hold, that tile is already showing its own summary"
                if hold_frames > 0
                else " -- with no hold, that tile is black"
            ),
        ),
        Moment(
            "last-picture",
            at(HEAD_PAD_SECONDS + POST_BEEP_SECONDS) - 1,
            "the last frame with any live picture in it, one frame before the tail pad",
        ),
        Moment(
            "last-action",
            action_frames - 1,
            f"the action's final frame -- inside the {TAIL_PAD_SECONDS:g}s tail pad"
            + (
                ", so every tile is already showing its summary"
                if hold_frames > 0
                else ", so black on every tile"
            ),
        ),
```

- [ ] **Step 2: Check `hold_frames` is in scope there**

Read the function around line 120. `hold_frames` is used at line 165, so
it is in scope at 148 -- confirm it is assigned above the `per_stage`
list and not between it and line 165. If it is assigned later, move the
assignment above `per_stage`.

- [ ] **Step 3: Run the script both ways and look at the frames**

```bash
uv run python scripts/render_grid_frames.py --help
```

Then run it once with a hold and once without, per its own `--help`, and
open the `short-tile-ends` and `last-action` frames from the held run.

Expected: no black tiles in either frame; the short tile carries its
summary while the others are still live in `short-tile-ends`.

- [ ] **Step 4: Publish the two frames as an Artifact for review**

This host is headless -- local image files do not reach the user. Put the
`short-tile-ends`, `last-action` and `hold-mid` frames from the held run
side by side in one HTML page with a one-line caption each, and publish
it with the `Artifact` tool. Load the `artifact-design` skill first.

- [ ] **Step 5: Commit**

```bash
uv run ruff check scripts/render_grid_frames.py
git add scripts/render_grid_frames.py
git commit -m "docs(compare): stop the frame-review script describing tiles as black"
```

---

### Task 6: Full suite and branch review

- [ ] **Step 1: Run the whole suite**

```bash
uv run pytest -q
```

Expected: green. It takes around 220s in parallel.

- [ ] **Step 2: Run the integration suite the way CI does**

```bash
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -m integration -n0 -q
```

Expected: green, with nothing skipped. A skip is a failure under that
env var.

- [ ] **Step 3: One whole-branch read over the seams**

The seam this change lives in is the order of the video half:
`xstack` -> sprite overlay -> clock drawtext -> early summary -> concat
-> `format=yuv420p`. Read `_build_filter_graph` top to bottom once,
against `_video_tail`'s and `_clock_filters`' docstrings, and check that
no docstring still claims something the rewiring made false. Both were
edited in Tasks 2 and 3; this is the pass that catches the one that was
not.

- [ ] **Step 4: Request a code review**

Use the `superpowers:requesting-code-review` skill. Give the reviewer:

- The spec (`docs/superpowers/specs/2026-08-08-per-tile-early-summary-design.md`)
  and this plan.
- The specific claims to verify, named:
  1. That cropping the composed still per cell is exact -- that no
     element of `overlay_html.grid_html` crosses a cell boundary and no
     cell's content depends on another shooter. The spec asserts this
     from `overflow: hidden` and `_cell_groups`; treat it as unverified.
  2. That `tile_footage_end_seconds` is the same instant the tile chain's
     black `tpad` starts, for both the seeking and the lead-padded tile.
  3. That the early overlays cannot reach a hold frame.
  4. **That each new test genuinely fails against the pre-change code.**
     Task 4 Step 8 records two mutations; ask the reviewer to re-run
     them rather than take the record on trust.
- The note that the implementation report is unverified and that a stated
  rationale never downgrades a finding's severity.
