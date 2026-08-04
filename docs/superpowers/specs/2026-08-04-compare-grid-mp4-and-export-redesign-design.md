# Compare-grid MP4 render + Export page redesign

**Date:** 2026-08-04
**Status:** Design approved, pending implementation plan

## Problem

A user with a merged match of 4 shooters wants one watchable video: a
2x2 grid, beep-aligned, with each shooter's shot count and splits burned
in, and a live ranking so the stage reads as a race. Today they cannot
get it.

What already exists:

- `splitsmith compare export <match-folder> --audio-from <shooter> -o
  out.fcpxml` renders the grid as FCPXML. Fixed alphabetical slots,
  black filler for missing trims, one unmuted tile.
- `splitsmith match trims <match>` produces the lossless per-stage trims
  the grid consumes, from a beep and a stage time alone.
- `/match/:matchId/compare/:stage` browses a synced grid in the SPA.

What is missing:

1. **No rendered video.** The grid exists only as an FCPXML timeline.
   The user's primary deliverable is an MP4 that needs no Final Cut.
2. **No splits in the grid.** `compare/emitter.py` never reads shot data
   by design, and `overlay_render.py` draws one shooter, not N.
3. **No UI path.** The Export page's "Multi-shooter compare grid" mode
   is hard-`disabled` with a `#328` badge and `canExport` excludes
   `mode === "compare"`. #328 shipped as the browsing page; the export
   was never wired.
4. **The Export page cannot host it.** The page is scoped to one
   shooter, so a multi-shooter mode has nowhere to live.

## Priority

**The MP4 grid is priority 0 and needs to produce a video today,
drivable from the local-mode UI.** Everything else is sequenced behind
it.

Phase 0 is the bare grid -- decode N trims, scale, `xstack`, map audio,
encode -- plus the minimum UI to trigger it in local mode. It has no
dependency on sprites, overlays or transitions.

Explicitly deferred out of phase 0:

- **Hosted mode.** No `Storage` writes, no presigned URLs, no download
  deliverables, no export history. The new surface is wrapped in
  `DesktopGate`, the same way `Compare` already is. Hosted support is a
  later phase.
- **FCPXML from the new surface.** Phase 0 renders MP4 only. The
  existing `splitsmith compare export` FCPXML path keeps working from
  the CLI exactly as it does today.
- **The full two-axis Export page.** Phase 0 adds a match-scoped page
  carrying only the grid flow; the existing shooter-scoped
  `export/:slug` page is untouched.

Everything layered on top of the bare grid is **optional and defaults
to off**: splits overlay, transitions, title cards. A grid render must
never require them, and the phase-0 renderer must be correct with all of
them absent rather than merely tolerating their absence.

## Scope

One spec. The engine ships in usable increments; the UI comes last.

Out of scope, deliberately:

- The FCPXML grid is untouched. It keeps clean tiles with no overlay,
  and keeps its existing `-96dB` mute on non-audio tiles.
- `overlay_render.py`'s per-frame ProRes / hevc-alpha pipeline is
  untouched. Single-shooter exports behave exactly as they do today.
- The Composition IR and its three renderers (`fcpxml_gen`,
  `fcp7xml_render`, `mp4_render`) are untouched.

## Rejected approach: grid in the Composition IR

The IR looked like a natural fit -- `Stage` already holds a `primary`
plus `secondaries: tuple[ConnectedClip, ...]`, each with a
`Transform(scale, position)` and its own `beep_offset_seconds`, which is
close to a description of a grid. Routing the grid through it would
have yielded MP4, FCPXML and FCP7 XML from one path.

It was rejected for two reasons:

1. With the FCPXML grid staying on `compare/emitter.py`, a grid concept
   in the IR would have exactly one consumer. That is IR complexity
   bought for a single renderer.
2. `render_mp4` composes primary-plus-positioned-`overlay` (PiP). A
   regular grid wants `xstack`. That is a different filter graph, not a
   parameterization of the existing one. Forcing them together would
   make both harder to read.

Additionally, `Stage.primary` is a bare `Asset` with no transform field,
and `ConnectedClip` has no per-clip audio concept, so both would have
needed new fields serving one caller.

## Architecture

The `compare/` package gains a second renderer beside the first. Both
are fed by the loader and layout it already has, neither of which
changes:

```
compare/project_loader.py  --+--> compare/emitter.py    -> FCPXML (unchanged)
compare/layout.py          --+--> compare/mp4_grid.py   -> MP4 (new)
                                       ^
                                       |
                             compare/overlay_sprites.py
```

### `compare/overlay_sprites.py` (new)

Overlay content is a **step function over shot events**. Shot counter,
last split and ranking change only when someone fires. A 30-shot stage
has ~30 distinct states, not ~750 frames. So states are pre-rendered
once and reused, instead of drawing every frame.

1. Union every shooter's shot times into one event list, plus the beep
   at t=0 and each shooter's end-of-run.
2. Per event, PIL-draw **one grid-sized RGBA PNG** covering the whole
   frame: each tile's shot counter and last split, plus the ranking
   strip. A single drawing pass sees every shooter's data at once,
   which is the only reason a cross-shooter leaderboard is possible --
   four independent per-tile overlays structurally cannot compare.
3. Return an ordered list of `(png_path, duration_seconds)`.

This is ~30 PIL draws per stage instead of ~750, and produces no ProRes
intermediate. Sprite filenames are content-addressed from a hash of
layout, theme and shot data, so repeat exports and theme comparisons
read from cache.

**Overlay content:** per-tile shot counter and last split, plus a live
delta strip ranking shooters by elapsed time at the current event.

**Elapsed time is not in the sprites.** It ticks continuously, so
putting it there would defeat the whole design. It is drawn by ffmpeg
`drawtext` instead (see below).

### `overlay_text.py` (new, extracted)

`_load_font`, `_draw_text_with_shadow` and the bundled-font resolution
move out of `overlay_render.py` into a shared module imported by both
renderers. It sits at `src/splitsmith/overlay_text.py`, not under
`compare/`, because `overlay_render.py` is a top-level module and must
not import from a subpackage to reach its own helpers.
`overlay_render.py`'s behaviour does not change; this is a move, not a
rewrite.

### `compare/mp4_grid.py` (new)

One ffmpeg invocation per stage, then a `concat`-demuxer stitch with
`-c copy` across stages -- the same two-phase structure `mp4_render`
already uses, and for the same reason (the final stitch must not
re-encode).

Per-stage graph:

- **Inputs:** N shooter trims, each `-ss` to its beep-aligned start and
  `-t` to the stage duration; plus the sprite PNGs as a single
  concat-demuxer input with per-state durations. PNG decodes to RGBA, so
  alpha survives without an intermediate video file.
- **Video:** `scale` each trim to tile size, `xstack` into the grid,
  `overlay` the sprite stream on top, then one `drawtext` per tile
  rendering elapsed time from a pts expression. ffmpeg caches glyphs, so
  the only genuinely per-frame element costs almost nothing and never
  touches PIL.
- **Audio:** each shooter's audio `-map`ped as its own output track,
  with the default disposition on the shooter chosen as the audio
  source.

Command construction is split into pure functions
(`_build_stage_command` / `_build_concat_command`) with an injectable
runner, mirroring `mp4_render` and `trim` so tests can assert the
invocation without shelling out.

### Output resolution

**Default canvas is 4K UHD (3840x2160), sized so every tile gets
1920x1080.** A 2x2 grid of 1080p tiles is exactly 4K, which makes this
the one canvas that is native for 1080p sources (no upscale) and a clean
2:1 downscale for 4K sources. At 1080p the same grid would give each
shooter 960x540, which is too small to read a shooter's hands on.

Larger grids keep the 4K canvas and take smaller tiles: 3x3 gives
1280x720, 4x4 gives 960x540. The canvas does not grow past 4K.

`compute_layout` already fits tiles with
`scale = min(cell_w / cam_width, cell_h / cam_height)`, so it needs no
change -- only the canvas dimensions fed to it.

This diverges from `compare/emitter.py`, which sets
`seq_width = seq_meta.width` and inherits the canvas from the audio
shooter's source. For 4K sources the two agree. For 1080p sources the
FCPXML grid produces a 1080p canvas with 540p tiles while the MP4
produces 4K with 1080p tiles. Aligning the FCPXML path is deliberately
left out of scope here; it is a one-line change to revisit once the MP4
grid is proven.

**Cost:** decoding N 4K streams and encoding one is the most expensive
thing in this design. A full match is plausibly tens of minutes of CPU
encode. 1080p is offered as the fast option in the UI and is the right
choice for a quick look. This is why per-stage failures must not
discard the whole run.

### Multi-track audio

MP4 only. Every shooter ships as a separate audio track. QuickTime, VLC
and Final Cut all switch tracks.

Phase 0 shipped with the `--audio-from` shooter's track as the default
and nothing else, which left YouTube, browser `<video>` and social
players -- all of which read track 1 only -- playing exactly one
shooter. **Phase 1b supersedes that**: a mix of every shooter is always
track 1 and always the default, and the per-shooter tracks follow as
2..N+1. See "Phase 1b -- merged audio track" below.

The FCPXML grid keeps its existing `-96dB` mute on non-audio tiles.

## Backend

A new match-scoped export endpoint. The existing `/api/match/export` is
shooter-scoped and stays as it is.

Request body: selected stage numbers, audio-source shooter slug,
per-shooter camera selectors, grid layout, overlay on/off, output format
(`mp4` or `fcpxml`).

It runs through the existing job queue like other exports, since a grid
re-encode is long-running. Trims are a precondition: the endpoint
reports which shooter/stage pairs are missing trims rather than
silently producing filler tiles for all of them.

## Export page redesign

### Diagnosis

The page conflates two independent axes. **Who is in the export** is
hardcoded to "this shooter" and never modelled, so the grid had nowhere
to live. **What you get** is split across a "mode" selector *and* a
format dropdown nested inside one format's row, so MP4 ended up as a
footnote to FCPXML.

Concrete defects, ordered by cost:

1. **Shooter-scoped page offering a multi-shooter mode.**
   `ExportInner({ slug })` loads `api.getProject(slug)` and PATCHes
   `/api/shooters/{slug}/compare-camera`; the route is
   `match/:matchId/export/:slug`. A 4-shooter grid cannot be configured
   from inside one shooter's page.
2. **"Camera for the grid" is a persisted shooter property inside an
   export mode.** Changing it PATCHes the server immediately, while
   every other control on the page does nothing until Export is
   pressed. Two interaction models in one form.
3. **MP4 is a "Variant" of the FCPXML row.** The row is titled FCPXML
   and described as a Final Cut timeline; its dropdown's third entry is
   "MP4 (rendered)". Selecting it leaves the summary rail still
   promising to write `name.fcpxml`, so the pre-flight check lies.
4. **Numbered sections that renumber.** Section 6 becomes section 3 in
   trims-only mode. Numbering promises a fixed sequence that does not
   exist.
5. **One button, two contracts.** `submitExport` blocks, polls and
   returns a result panel; `submitTrims` queues N jobs and hands off to
   the jobs rail with a text note.
6. **`overlay_codec` (auto / hevc-alpha / prores-4444) is in the UI.**
   An encoder detail on a page about deliverables.

### Structure

The two axes become explicit:

```
WHAT ARE YOU MAKING
  [ Editable timeline ]  [ Rendered video ]  [ Lossless trims ]
      FCPXML / FCP7            MP4              stream copy

WHO IS IN IT
  [ This shooter ]  [ All shooters ]

...then only the options that combination actually has.
```

"All shooters" means the compare grid for a timeline or a video, and a
batch trim of every shooter (what `splitsmith match trims` does today)
for trims.

Applicability:

| Option           | Timeline | Video | Trims |
|------------------|----------|-------|-------|
| Stage selection  | yes      | yes   | yes   |
| Trim padding     | yes      | yes   | yes   |
| Transitions      | yes      | yes   | no    |
| Title cards      | yes      | yes   | no    |
| Splits overlay   | see below| yes   | no    |
| All shooters     | yes      | yes   | yes   |
| Audio source     | grid only| grid only | no |

**Splits overlay interacts with the who-axis.** A single-shooter
timeline can have one (that is today's `overlay_render` ProRes path,
unchanged). A grid timeline cannot -- the FCPXML grid ships clean tiles
by decision. A grid video always can. The UI must hide the control for
timeline-plus-all-shooters rather than offering a toggle that does
nothing.

Changes:

- `match/:matchId/export` stops redirecting to a default shooter and
  becomes the real page. `export/:slug` still resolves, opening the page
  with that shooter preselected, so existing deep links keep working.
- Numbered sections are removed. The chosen combination determines which
  sections exist.
- Grid camera moves to shooter settings, where a persisted shooter
  property belongs.
- MP4 becomes a peer of FCPXML rather than a variant nested inside it.
- The summary rail derives its file list from the real combination
  instead of always promising a `.fcpxml` / `.csv` / `.txt` bundle.
- `overlay_codec` leaves the UI. `auto` resolution stays in the backend.
- The two submit contracts are made visible: a blocking render reports
  progress inline, a queued batch says so and points at the jobs rail.

## Error handling

- **Missing trim** for a shooter/stage: that slot renders as a black
  filler tile, matching `compare/emitter.py`'s existing rule. Slots
  never reshuffle.
- **Missing audit** for a shooter: the tile renders with no counter and
  no splits rather than failing the stage. The grid's value does not
  depend on every shooter being audited.
- **Per-stage ffmpeg failure**: reports and continues to the next stage,
  unlike `mp4_render` today, which fails the whole render. A 12-stage
  grid re-encode is too long to lose to one bad stage. The job result
  names which stages failed and why.
- **Unreachable source**: the existing `source_reachable` gate on the
  export overview applies unchanged.
- **No trims at all**: the endpoint refuses rather than rendering a grid
  of black tiles, and names what to run (`splitsmith match trims`).

## Testing

- **Sprite generation** is pure: from a fixture audit JSON, assert the
  event count, the state boundaries and the per-state durations. Golden
  hash on the rendered PNG for a pinned theme.
- **ffmpeg command construction** is pure: assert the filter graph and
  the `-map` arguments without shelling out, matching `mp4_render`'s
  existing test pattern.
- **Track mapping**: assert N+1 audio tracks -- the mix first, carrying
  the default disposition, then the shooters in alphabetical order.
- **Filler**: a shooter missing one stage's trim produces a black tile
  in a stable slot, and the other slots do not move.
- **One integration test** (`@pytest.mark.integration`) renders a short
  real grid with real ffmpeg from fixture clips, then probes the output:
  stream count, track count, duration, and that the overlay is actually
  visible in a sampled frame.

That last point is deliberate. Per the project's review practice, a
green assertion on a command string is not evidence the user sees
anything -- on #617 a fix reached the output and was ellipsized away
while the test passed. The integration test probes the rendered file.

## Sequencing

### Phase 0 -- bare grid MP4, CLI + local-mode UI (today)

The smallest thing that produces a watchable video from the app. No
sprites, no PIL, no overlay, no transitions, no hosted mode, no FCPXML.

1. `compare/mp4_grid.py`: per-stage ffmpeg call (N trims `-ss`-aligned,
   `scale`, `xstack`, N audio `-map`s, 4K canvas) plus the `concat`
   stitch. Pure command builders with an injectable runner.
2. `splitsmith compare export` gains `--format mp4`, reusing the
   existing match-folder source, `--audio-from` and `--camera` flags
   unchanged. At this point the engine is verifiable without any UI.
3. Match-scoped endpoint, local mode only: takes stage numbers, the
   audio-source slug, per-shooter cameras and the canvas size; runs
   through the existing job queue; reports missing trims by
   shooter/stage rather than silently emitting filler.
4. `match/:matchId/export` stops redirecting to a default shooter and
   becomes a real match-scoped page, wrapped in `DesktopGate`. It
   carries only the grid-MP4 flow: shooter list with the audio source
   picked, stage chips, canvas size, render button, job progress,
   reveal-on-disk. `export/:slug` continues to resolve to the existing
   shooter page, unchanged.
5. Command-construction unit tests + one real 4-shooter render.

Phase 0 is independently shippable and is the deliverable that matters
today. Step 4 is the redesign's foundation, not scaffolding: later
phases fold the shooter export into this page rather than replacing it.

### Phase 1 -- splits overlay (opt-in)

4. `overlay_text.py` extraction from `overlay_render.py`
   (behaviour-preserving, no new features).
5. `overlay_sprites.py` with unit tests.
6. Wire sprites + `drawtext` clock into `mp4_grid.py` behind an
   `--overlay` flag that defaults to off.

### Phase 1b -- merged audio track (requested after phase 0 shipped)

The grid currently ships one audio track per shooter and nothing else.
YouTube, browser `<video>` and every social player read **track 1 only**,
so a shared grid plays exactly one shooter -- which makes the whole
multi-track design invisible to anyone the video is sent to.

- A mixed track is **always** present as track 1 and carries the
  `default` disposition; the per-shooter tracks follow as 2..N+1.
- Mixing is `amix` with `normalize=1`. It cannot clip, and because the
  sources are uncorrelated the perceived loudness stays close to a single
  shooter. Known weakness, accepted: a stretch where only one shooter is
  active still sits at 1/N.

Three consequences that are not optional to handle:

- The **stream-count invariant moves from N to N+1**. That is the rule
  `concat -c copy` enforces, so every assertion counting audio tracks
  moves with it -- including the empty-cell test that specifically checks
  an unfilled grid cell adds *no* audio track.
- **`--audio-from` loses its primary job.** With the mix always default,
  it only drives frame-rate derivation. Its help text and docstring
  currently describe it as choosing which track plays first, which
  becomes false.
- The **handoff doc's probe expectations change** -- it tells the next
  session to expect exactly one track per shooter with the default on the
  chosen one. A stale verification checklist is worse than none.

### Phase 2 -- titles, transitions, and summary screens (opt-in)

Everything here is optional and defaults to off. All of it forces a
re-encode at the concat seams, which phase 0's `-c copy` stitch
deliberately avoids -- so it is a separate code path, not a parameter on
the existing one.

7. **Transitions between stages** and **stage title cards**.
8. **Opening title screen** and **end summary**.
9. **Per-shooter post-stage summary screen**, with a configurable hold
   duration, showing that shooter's splits alongside their scoring for
   the stage.

**Reuse, do not rebuild.** `composition.py` already models
`TitleCard` (text, duration, style, font), `Transition` (kind, duration)
and `Segment` (intro / outro), and the single-shooter renderers already
consume them. The grid path needs its own rendering of these concepts,
but the IR vocabulary and the FCPXML semantics exist -- diverging from
them would give the two exporters different ideas of what a title is.

**The scoring data is already on disk.** `StageEntry.scorecard`
(`ui/project.py`) persists a full `StageScorecard` per shooter per
stage -- `hit_factor`, `stage_points`, `stage_pct`, `alphas`,
`charlies`, `deltas`, `misses`, `no_shoots`, `procedurals`, `dq` --
populated by `merge_stage_times` from the SSI Scoreboard, plus
`stage_rounds` for round counts. This matters because **the renderer is
offline batch and must not call a network service mid-render**: the data
lives in the same `MatchProject` the grid already loads.

(The example JSON under `examples/` carries times only. That is the
export format, not what a project stores -- do not design against it.)

Degradation is therefore narrow but real: `scorecard` is `None` for
placeholder stages and for projects imported before the field existed,
and manually-timed stages carry `time_seconds_manual` with no scorecard.
A summary must render what is present and never imply a number it does
not have.

**Ranking must not be invented.** Per the scoreboard tooling's own
rules, competitors are never ranked by raw points -- points are
meaningless across stages and divisions. The correct field,
`stage_pct`, is already persisted, so the rule costs nothing to honour.
Do not reach for `stage_points` because it is the more obvious-sounding
name.

### Where the two overlays live in the frame

They coexist, so they are one design, not two:

- **During a stage:** the live per-tile overlay -- shot counter, last
  split, running clock -- draws on top of each tile, per phase 1.
- **After a stage:** each tile **freezes on its last frame, blurred and
  dimmed**, and that shooter's stage summary draws over their own cell.
  The grid layout persists so each shooter's numbers land where the
  viewer has been watching them. Held for a configurable duration.
- **End summary:** the same treatment with match-level figures.

Two consequences fall out of the frozen-tile approach:

- **The blur is computed once, not per frame.** The tile is a still, so
  the blurred, dimmed frame is rendered once per stage and held --
  the same economy as the pre-rendered sprites. Applying `gblur` to
  every frame of a multi-second hold at 4K would cost orders of
  magnitude more for an identical result.
- **The hold extends the stage segment rather than adding a new one.**
  The summary lives inside the stage's own segment, so the stitch stays
  dumb and the stream-layout invariant is untouched. The duration model
  grows by the hold; `build_stage_plans` owns that.

**The live overlay stops at the freeze and hands off.** It does not
persist into the hold -- a frozen shot counter beside a stopped clock is
what the filter graph would do by default, and it reads as a stall
rather than a conclusion. At the freeze the live overlay ends and the
summary takes its place, presenting that shooter's splits statistics for
the stage.

That handoff has a useful consequence for the sprite architecture: the
live overlay's sprite sequence terminates at the last shot event, and
the summary is a **single additional static sprite** held for the
configured duration. It is not a new kind of thing -- it is one more
state in a sequence that is already a step function, so it costs one
PIL draw per stage.

### Phase 3 -- hosted mode + full Export page

8. Hosted-mode support for the match-scoped endpoint: `Storage` writes,
   download deliverables, removal of the `DesktopGate`.
9. Fold the shooter-scoped export into the match-scoped page and
   complete the two-axis restructure described above.
10. FCPXML as a peer output on the new surface.
11. Integration test covering the full path.
