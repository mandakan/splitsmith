# Compare Grid -- Phase 1 kickoff

Entry point for a session starting fresh on the compare-grid splits overlay.
Assumes no memory of the sessions that built phases 0 and 1b.

**Read first:** `docs/superpowers/specs/2026-08-04-compare-grid-mp4-and-export-redesign-design.md`
-- sections "Architecture", "Phase 1", "Phase 2", and "Where the two
overlays live in the frame". That spec is the contract; this document is
orientation plus the things that only came out of building the earlier
phases.

## What already ships on `main`

```bash
splitsmith compare export <match> --format mp4 --audio-from "<shooter>" -o grid.mp4
```

Renders N shooters' beep-aligned per-stage trims into one grid video:

- 4K canvas (3840x2160); each tile in a 2x2 gets 1920x1080. Frame rate
  follows the audio-source shooter's footage.
- Audio track 1 is a **mix of every shooter** (`amix normalize=1`),
  carrying the `default` disposition. Per-shooter tracks follow as
  2..N+1, named via `handler_name`, in alphabetical slot order.
- A shooter missing a stage's trim gets a black tile that keeps its cell
  plus a silent audio track. Grid cells no roster member reaches are
  filled black.
- A stage whose render fails is reported and skipped; the rest still
  stitch. All stages failing raises rather than writing a zero-byte file.

Also reachable from the app at `match/:matchId/export` (local mode,
`DesktopGate`-wrapped), which queues a job through the existing queue.

`compare/emitter.py` (the FCPXML grid) and `overlay_render.py` (the
single-shooter ProRes overlay) are **untouched by all of this and must
stay that way** -- several tests and the FCPXML path depend on it.

**Verified on real footage:** a 12-stage, 4-shooter match, cross-tile
beep alignment within ~one frame at 59.94fps, stable slots, correct track
identity, sound-vs-picture flat at ~-12ms across the whole match.

## Baselines to hold

| | |
|---|---|
| Unit | 2453 passed / 20 skipped |
| Integration | 19 passed / **0 skipped** |
| SPA | 63 passed, `tsc` clean |

**Integration must stay at 0 skipped.** CI installs ffmpeg (#670/#671)
and *fails the build if any integration test skips* -- a skipped suite
used to read as success, which is how a whole class of defect stayed
invisible.

Run with:
```bash
uv run pytest -m "not integration" --ignore=tests/test_hosted_docker_smoke.py
uv run pytest -m integration --ignore=tests/test_hosted_docker_smoke.py
```
`tests/test_hosted_docker_smoke.py` may fail locally on a MinIO port
conflict; unrelated to this work.

CLI tests that assert on `--help` output must use `strip_ansi()` from
`tests/conftest.py` -- rich detects `GITHUB_ACTIONS` and interleaves ANSI
escapes, so a literal substring check passes locally and fails on CI.
Reproduce with `GITHUB_ACTIONS=true uv run pytest ...`.

## What phase 1 is

The splits overlay **and** the summary screens, as one design. They
coexist in the same frames, so designing them apart would have them
fighting over the same pixels and the same sprite machinery.

- **During a stage:** per-tile shot counter, last split, running clock,
  plus a live delta strip ranking the shooters.
- **After a stage:** each tile freezes on its last frame, blurred and
  dimmed; that shooter's stage summary draws over their own cell, held
  for a configurable duration. The live overlay **stops** at the freeze
  and hands off -- it does not persist into the hold.
- **End summary:** same treatment with match-level figures.

Everything is opt-in and defaults to off. A grid render must be correct
with all of it absent, not merely tolerate its absence.

### The architecture is already decided

Read the spec for the reasoning; the short version:

- Overlay content is a **step function over shot events**, not a
  per-frame animation. A 30-shot stage has ~30 states. Pre-render one
  grid-sized RGBA PNG per state (`compare/overlay_sprites.py`), feed them
  as a single concat-demuxer input, composite with one `overlay` filter.
  ~30 PIL draws per stage instead of ~750.
- **The ticking clock is not in the sprites** -- it is ffmpeg `drawtext`
  with a pts expression. ffmpeg caches glyphs, so the one genuinely
  per-frame element never touches PIL.
- **The summary is one more static sprite state**, held for the
  configured duration. Not a new kind of thing.
- **The freeze-blur is computed once**, not per frame -- the tile is a
  still, so blurring every frame of a multi-second 4K hold would cost
  orders of magnitude more for an identical result.
- **The hold lives inside the stage's own segment.** The stitch stays a
  dumb `concat`; what grows is the duration model in `build_stage_plans`.
- `overlay_text.py` is extracted from `overlay_render.py` (font loading,
  shadowed text) as a **behaviour-preserving move**, shared by both.

### The scoring data is already on disk

`StageEntry.scorecard` (`ui/project.py`) persists a full
`StageScorecard` per shooter per stage: `hit_factor`, `stage_points`,
`stage_pct`, `alphas`, `charlies`, `deltas`, `misses`, `no_shoots`,
`procedurals`, `dq`. Plus `stage_rounds`. Populated by
`merge_stage_times` from the SSI Scoreboard.

This matters because **the renderer is offline batch and must not call a
network service mid-render**. Do not design against the JSON in
`examples/` -- that is the export format and carries times only.

Degradation cases that are real: `scorecard` is `None` for placeholder
stages and pre-scorecard projects; manually-timed stages carry
`time_seconds_manual` with no scorecard. Render what is present; never
imply a number that is absent.

**Ranking is `stage_pct`, never `stage_points`.** Raw points are
meaningless across stages and divisions. The correct field is already
persisted, so the rule costs nothing.

## Invariants that must survive

1. **Stream layout is uniform across segments.** 1 video stream at the
   canvas size and pinned frame rate, plus **N+1** audio streams (mix
   first, then shooters alphabetically). `concat -c copy` refuses
   segments that disagree, and it fails at the *very last step* after all
   the encode time is spent. Empty grid cells add video only.
2. **Beep alignment.** Every tile's beep lands at `head_pad_seconds`, for
   clamped, unclamped and filler tiles. This broke once when a filter
   reordering put `setpts` ahead of `tpad`; treat any reordering of the
   tile chain with suspicion.
3. **No cumulative A/V drift.** Segments carry PCM audio and the stitch
   does a single AAC encode (`-c:v copy -c:a aac`). Do not reintroduce
   per-segment AAC -- its encoder priming accumulated to +386ms by stage
   12. There is an integration test; keep it passing.
4. **Track identity.** MP4 discards `title=`; `handler_name=` is what
   lands.

## How this codebase gets verified

Eight defects were found across phases 0 and 1b. **Every one reached a
green test suite. None were found by reading code.** This is not a
stylistic preference -- it is the only method that has worked here.

- **Mutate your finished code** and confirm each test goes red on the
  test that claims to cover it. Tests that cannot fail are a finding even
  when everything is green.
- **Render and measure.** Assertions on ffmpeg arg tuples miss ordering
  bugs, container-metadata lies, and anything visual.
- **Choose fixture dimensions that can express the failure.** Two
  defects were invisible to every fixture on the branch: green empty
  cells only appear on rosters that do not fill the grid (3, 5, 7, 8,
  10-15 shooters), and audio drift only becomes visible past ~8 stitched
  segments. Everything used 2 or 4 shooters and 1-2 stages.
- **Container metadata lies.** `ffprobe` once reported a 21ms A/V
  difference on a file that was 372ms out. Measure decoded samples
  (`nb_frames * 1024 / sample_rate`), honouring the edit list, and
  measure sound against picture -- not declared durations.
- **`silencedetect` trusts the same lying sample table.** It reported a
  broken file as fine.

A scratch harness for rendering and probing real grids lives at
`~/.claude-tmp/gridcheck/` (`render_grid.py`, `render_grid3.py` for the
empty-cell case, `measure_drift.py`). Not part of the repo; recreate
freely.

## Environment

- ffmpeg 6.1.1 at `/usr/bin/ffmpeg`. The renderer resolves it through
  `splitsmith.runtime` -- never hardcode a binary. Verified against
  6.1.1, 7.0.2 and 8.1.2.
- The user's real 4-shooter match lives on **another machine**
  (`/Volumes/X9/matches/hfo-masters-2026`), so end-to-end verification on
  real footage has to be handed to that host. See
  `2026-08-04-compare-grid-mp4-phase-0-handoff.md` for that flow; update
  its `ffprobe` expectations if phase 1 changes the output shape.
- Node 22 in CI and locally (jsdom needs it).

## Open follow-ups

- **#672** -- a shooter whose stage is skipped, has no `beep_time`, or is
  absent from their project renders an unexplained black tile under a
  "rendered all N stages" headline. `project_loader` only records a
  `MissingTrim` for a narrower case.
- **#673** -- `test_new_ulid_returns_distinct_sortable_ids` is flaky;
  `ulid.ULID()` is not monotonic within a millisecond.

Neither blocks phase 1.

## Suggested first move

Start with the `overlay_text.py` extraction -- behaviour-preserving, no
new features, guarded by the existing suite. It de-risks everything
after it and makes the first sprite work cheap.

Then `overlay_sprites.py` as a pure function over shot events, tested
without touching ffmpeg: event count, state boundaries, per-state
durations, and a golden hash for a pinned theme. Only then wire it into
the filter graph.
