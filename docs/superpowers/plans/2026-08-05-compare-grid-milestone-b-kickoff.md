# Compare Grid -- Milestone B kickoff (the post-stage summary hold)

Entry point for a session starting fresh on the stage summary screens.
Assumes no memory of the sessions that built phases 0, 1b and Milestone A.

**Read first:** `docs/superpowers/plans/2026-08-04-compare-grid-phase-1-splits-overlay.md`,
**Tasks 7, 8 and 9 only**. That plan is the contract and its task bodies
carry the code; this document is orientation plus what only came out of
building Milestone A. The design behind it is
`docs/superpowers/specs/2026-08-04-compare-grid-mp4-and-export-redesign-design.md`,
sections "Phase 2" and "Where the two overlays live in the frame".

Regenerate the task briefs with the subagent-driven-development skill's
`scripts/task-brief <plan> 7|8|9`. The old workspace under
`.superpowers/sdd/` is git-ignored and may be gone.

## What ships on `main` (as of #677, `ef51e06`)

```bash
splitsmith compare export <match> --format mp4 --overlay \
  [--overlay-theme splitsmith|clean] --audio-from "<shooter>" -o grid.mp4
```

On top of the bare 4K grid (phase 0) and its merged audio track (phase
1b), an **opt-in** live overlay: per-tile shot counter, per-tile last
split, per-tile running clock. Everything it draws is per tile.

**There is no live delta strip, and there must not be one.** A
full-width band ranking the shooters was built and then removed after
the user watched it on real match footage: a beep-aligned tiled
composite already shows the race -- the tiles are synchronised, so who
is ahead is visible directly -- and a ranked list competes with the
thing it describes while its band overlaps the bottom row of tiles.
**Cross-shooter ranking is Milestone B's job**, in the stage summary
below, where the picture has frozen and the run is complete, and it
ranks by `stage_pct` off the scorecard -- a different computation, not a
relocation of the strip's live per-event elapsed time. `TilePanel`
carries no `rank` or `delta_to_leader` for the same reason; do not
reintroduce them for the summary.

Overlay content is a **step function over shot events**. States are
pre-rendered once as canvas-sized RGBA PNGs (content-addressed, so
identical states share a file) and fed to ffmpeg as one concat-demuxer
input composited by a single `overlay` filter. The clock is `drawtext`
with a pts expression and never touches PIL.

### The interfaces Milestone B consumes

`src/splitsmith/overlay_text.py` -- font resolution and shadowed text,
shared by the single-shooter overlay and the grid.
`_load_font`, `_draw_text_with_shadow`, `available_font_names`,
`reset_font_log_cache`, `OverlayRenderError`, and
`materialize_font(font_name, dest_dir) -> Path` (ffmpeg's `drawtext`
needs a real path that outlives the call).

`src/splitsmith/compare/overlay_data.py`

```python
TileShot(time_from_beep: float, split: float)

TileStageData(
    label: str, stage_number: int,
    shots: tuple[TileShot, ...] = (),
    stage_time_seconds: float | None = None,
    stage_time_is_manual: bool = False,
    scorecard: StageScorecard | None = None,
    stage_rounds: StageRounds | None = None,
)  # properties: shot_count, has_shots, last_shot_time

load_overlay_data(shooters) -> dict[tuple[str, int], TileStageData]
```

**This is the module Task 8's summary reads.** Everything the summary
needs -- hit factor, `stage_pct`, the hit counts, the DQ flag, the round
count, the manual-time flag -- is already on `TileStageData.scorecard`
and already loaded offline. Do not add a second loader, and do not reach
for the network.

`src/splitsmith/compare/overlay_sprites.py` --
`TilePlacement`, `TilePanel`, `OverlayState`, `build_overlay_states`,
`SpriteGeometry`, `render_state`, `write_sprite_sequence`,
`quantize_durations`, `write_concat_list`.

`render_state` draws inside the cells and nowhere else, so a state where
nobody has fired yet returns a **fully transparent** canvas. Any
assertion that "the sprite reached the pixels" therefore has to sample a
moment where a counter or a split genuinely exists, or it passes against
a renderer that draws nothing at all.

`src/splitsmith/compare/mp4_grid.py` --
`TileClock`, `StageOverlayPlan(sprite_list_path, font_path, font_size,
clocks, ink, stroke)`, `build_stage_command(..., overlay=None)`,
`render_grid_mp4(..., overlay=False, overlay_theme="splitsmith")`,
plus `_overlay_data_for_stage`, `_stage_overlay_plan`, `_clock_filters`.

## Baselines to hold

| | |
|---|---|
| Unit + integration, one run | 2645 passed / 20 skipped |
| Of which integration | 27 ran / **0 skipped** |

The suite runs in parallel by default (`addopts` carries `-n auto --dist
load`), so a full run is ~1m40s rather than ~4m35s. `-n0` restores serial
execution for debugging. Serial and parallel must report **identical**
counts -- a parallel run that passes while collecting fewer tests is a
silent regression, so check the counts, not just the colour.

```bash
uv run pytest -m "not integration" --ignore=tests/test_hosted_docker_smoke.py -q
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -m integration --ignore=tests/test_hosted_docker_smoke.py -v
uv run ruff check src tests && uv run black --check src tests
```

Measured on `main` at `94b0559`. `tests/test_hosted_docker_smoke.py` may fail
locally on a MinIO port conflict; unrelated, hence the ignore.
**Integration must stay at 0 skipped** -- CI fails the build on a skip.

CLI tests that assert on `--help` must use `strip_ansi()` from
`tests/conftest.py` and be run under `GITHUB_ACTIONS=true` too; rich
interleaves ANSI escapes when it detects CI.

## Amendments to Tasks 7-9

The plan's task bodies were written before Milestone A shipped. They are
still the contract, with these corrections. Read them together.

### Task 8 gains a ranking. The plan has none.

Task 8 as written is per-shooter only. The user has since decided **the
stage summary ranks shooters against each other** -- it is where the
live delta strip's job went after that strip was removed.

- Rank by **`stage_pct`**, never `stage_points`. Raw points are
  meaningless across stages and divisions and `stage_pct` is already
  persisted on `TileStageData.scorecard`.
- Each shooter's placing draws on **their own cell**, beside the figures
  Task 8 already specifies, so the numbers land where the viewer has been
  watching that shooter.
- Rank only tiles that have a `scorecard` with a `stage_pct`. A tile with
  no scorecard, a filler tile, and a DQ'd shooter are three different
  absences; none of them gets an invented placing.
- **Do not** reintroduce `TilePanel.rank` or `TilePanel.delta_to_leader`.
  Those were the live strip's per-event elapsed-time computation and were
  deleted with it. The summary's ranking is a different thing computed
  from the scorecard at the freeze.
- **Whether the summary also shows a time delta is deliberately open.**
  Build the ranking first, render it on real footage, then decide. The
  strip's problem was only visible once rendered.

### Task 9: the clock will bleed into the hold. Verified, not theoretical.

Task 9 says to check that the live overlay stops at the freeze. It does
not, as the code now stands. Both filters in `_clock_filters`
(`src/splitsmith/compare/mp4_grid.py`) are unbounded above:

- the open-ended ticking clock, `enable='gte(t,{start})'`
- the static hold, `enable='gte(t,{freeze})'`

Either will run straight through the summary. A clock ticking over a
blurred, dimmed summary -- or frozen beside it -- is precisely the "reads
as a stall rather than a conclusion" failure the spec calls out. Cap both
at the action's end (`duration_seconds`), and prove it by extracting a
frame from inside the hold and confirming no clock glyphs are present,
not by reading the expressions.

The sprite chain's `trim` already ends at `duration_seconds`, so the
sprite half stops correctly on its own. Only the `drawtext` half needs
the cap.

### What else moved under these tasks

- **`SpriteGeometry` has no `strip_height`.** Any layout arithmetic in
  Task 8 that reserved room for a bottom band should use the full cell.
- **A sprite state can be fully transparent** now that nothing draws
  outside the cells. Assertions that "the overlay reached the pixels"
  must sample a moment where a counter, split or summary figure genuinely
  exists.
- **The summary is pure PIL**, so unlike the running clock it needs no
  `drawtext` and survives an ffmpeg built without libfreetype untouched.
  A host that loses the clock still gets full summaries. Say so in the
  degradation notice if the wording still fits.
- **`render_grid_mp4` now probes ffmpeg capabilities before rendering.**
  Freeze-frame extraction is `-frames:v 1`, which needs nothing special,
  but the hold must not break the `option framerate` concat contract the
  sprite input depends on.

## What Milestone B is

Tasks 7-9 of the plan: at the end of each stage every tile **freezes on
its last frame, blurred and dimmed**, and that shooter's stage summary
draws over their own cell, held for a configurable duration.

- **Task 7** -- the duration model. `GridStagePlan` gains
  `hold_seconds` and a `total_seconds` property.
- **Task 8** -- `compare/overlay_summary.py`: extract one freeze frame
  per tile, blur it **once** in PIL, composite the canvas-sized still.
- **Task 9** -- wire the hold into the graph, add `--summary-hold`, and
  extend the integration test.

Three decisions already made, with reasons, that should not be reopened:

- **The blur is computed once, not per frame.** The tile is a still.
  `gblur` with an `enable` expression on every frame of a multi-second 4K
  hold costs orders of magnitude more for an identical result. If you
  find yourself adding a blur filter to the graph, you took the wrong
  path.
- **The hold lives inside the stage's own segment**, concatenated after
  the action, so the cross-stage stitch stays a dumb `concat -c copy`.
- **The live overlay stops at the freeze and hands off.** A frozen shot
  counter beside a stopped clock reads as a stall, not a conclusion. The
  sprite sequence already ends at the last shot event; the summary is one
  more static sprite state.

**Ranking is `stage_pct`, never `stage_points`.** Raw points are
meaningless across stages and divisions. Never render a number that is
absent: `scorecard` is `None` for placeholder stages and pre-scorecard
projects, and a manually-timed stage carries `time_seconds_manual` with
no scorecard.

## Invariants that must survive

1. **Uniform stream layout.** 1 video stream at canvas size and pinned
   frame rate, plus **N+1** audio streams (mix first, then shooters
   alphabetically). `concat -c copy` refuses segments that disagree, and
   it fails at the very last step after all the encode time is spent.
   The hold must extend **every stream in the segment together** --
   never add or drop one.
2. **Beep alignment.** Every tile's beep lands at `head_pad_seconds`.
   This broke once when a reordering put `setpts` ahead of `tpad`.
3. **No cumulative A/V drift.** Segments carry PCM; the stitch does one
   AAC encode. Per-segment AAC once accumulated to +386ms by stage 12.
4. **Track identity.** MP4 discards `title=`; `handler_name=` lands.
5. **Default-off.** With no overlay and no hold, `build_stage_command`
   and `build_concat_command` must emit **byte-identical argv** to
   `ef51e06`. Milestone A verified this across 22 configurations
   (2/3/5/6 shooters x 0/1/2 fillers x horizontal/vertical); keep a
   test that would catch a regression.

## How this code actually gets verified

Every defect that mattered on Milestone A was found by rendering and
measuring, several by mutation, **none by reading**.

- **Render and measure.** Assertions on ffmpeg arg tuples miss ordering
  bugs, container-metadata lies and anything visual.
- **`ffprobe` lies.** It once reported a 21ms A/V difference on a file
  that was 372ms out. Measure decoded samples honouring the edit list.
  `silencedetect` trusts the same table.
- **Choose fixture dimensions that can express the failure.** A 2x2
  preview once hid a delta-strip defect that only appeared at 3x3 and
  4x4 (the strip is gone now, but the lesson stands). Rows/cols swaps
  are no-ops on square grids. Use a **3-shooter** roster (one unreached
  cell), at least one shooter with **no audit**, and a non-square grid.
- **Sample frames by index, not by seeking.** `-ss` fast-seeking to a
  timestamp that equals a frame's pts breaks the tie unpredictably and
  produced a 4% flaky test. Use `select=eq(n,N)`.

### Four ways a mutation check silently lies here

Five mutation steps in the Milestone A plan could not fail, every one
caught by an implementer rather than by the suite. On top of that:

1. **The pyc cache.** CPython invalidates `.pyc` on mtime-in-*seconds*
   plus size, so a same-byte-length mutation applied and reverted inside
   one second is never applied at all. **Purge `__pycache__` on both
   sides of every mutation.**
2. **`git checkout <file>`** reverts your uncommitted fix along with the
   mutation, so the "after" run measures code you already deleted.
3. **The content-addressed sprite cache** serves pre-mutation PNGs into
   a reused work dir, so the mutated renderer never runs. Use a fresh
   work dir per mutation run.
4. **A fixture that cannot express the failure.** If the mutation leaves
   the suite green, change the fixture before concluding anything.

## Known issues you will meet

- The ticking clock reads one hundredth low on ~4.6% of frames from
  float truncation inside the ffmpeg expression. Measured over 95,132
  simulated frames: monotonic, zero backward steps, held value never
  below the last ticked one. Deliberately unfixed -- an epsilon only
  reaches 2.52% and does not converge. The reasoning is recorded at
  `_clock_filters`.
- The concat demuxer needs `option framerate` per entry or it snaps
  state boundaries to a 25fps time base. Verified on ffmpeg 6.1.1 and
  7.0.2. An ffmpeg that does not know the `option` keyword fails loudly
  (`unknown keyword`, exit 183) -- but an unknown option *name* under a
  supported keyword is silently ignored, so renaming `framerate` would
  fail silently.
- The overlay is **CLI-only**. `ui/server.py`'s `CompareGridRequest` has
  no overlay field. That is phase 3 territory.
- `TileShot.split` is recomputed over the time-sorted sequence, so it
  can differ from the engine's `Shot.split` on an audit whose
  `shot_number` order disagrees with time order. Documented in
  `overlay_data._load_shots`.

## Environment

- ffmpeg 6.1.1 at `/usr/bin/ffmpeg`, resolved through
  `splitsmith.runtime` -- never hardcode a binary.
- The user's real 4-shooter match lives on **another machine**
  (`/Volumes/X9/matches/hfo-masters-2026`), so end-to-end verification on
  real footage is handed to that host. See
  `2026-08-04-compare-grid-mp4-phase-0-handoff.md`, and update its
  `ffprobe` expectations -- a hold changes durations, and a stale
  verification checklist is worse than none.
- Node 22 in CI and locally.
