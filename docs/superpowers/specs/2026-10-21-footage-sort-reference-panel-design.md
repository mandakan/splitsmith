# Footage sort: compare a clip with a sorted shooter

Builds on `2026-10-01-footage-sort-design.md` (the sort page, its engine,
`ui/footage_sort_api.py`, `pages/FootageSort.tsx`, `lib/footageSort.ts`).

## Problem

When the sort cannot place a clip (camera clock unknown, no scorecard
times, two runs seconds apart), the user has to recognise the stage by
eye. Today that means switching between the sort page and another
shooter's Audit view, stage by stage, many clicks per clip. Black Handgun
2023: every clip from one phone had no clock, and the scoreboard times had
not come in, so seven clips were placed by flipping to Mathias's Audit for
each.

Sorting into the right shooter is a separate decision the user already
makes well; this is only about finding the stage.

## Goals

- From a clip's panel on Sort footage, see every stage of the match at a
  glance as a frame from a shooter who already has footage on it, and play
  any of those next to the clip being assigned.
- Know which stages the shooter being assigned still lacks, so the hunt is
  among two or three, not twelve.
- Set the clip's stage from the reference in one click.
- Works both before anyone is imported (the reference comes from this
  sort's confident clips) and after (from a shooter's project).

## Non-goals

- Assigning the shooter, or assigning anything on its own: the reference
  sets the stage picker, the user still presses Use this.
- Fixing camera clocks: anchoring stays the mechanism; this finds the one
  answer faster.
- Stage descriptions or course-of-fire text (not stored; a later stage
  editor's concern).
- Per-shooter shooting order as a matching input (a separate project, the
  stage metadata editor).
- Hosted mode: the sort is local only and so is this.
- Phone layout: the panel needs two players side by side; below `md` the
  Compare button is not shown.

## Anti-goals

- A request that runs ffmpeg. Frames are made by the sort's background
  pass, like its thumbnails and strips (`media.md`).
- A second "Use this" primary. One primary per view stays the clip
  panel's.

## What the user sees (decided)

Mockup B from the brainstorm (`.superpowers/brainstorm/.../reference-panel.html`,
screenshot `reference-panel-mockup-2.png`):

- The clip panel (`AssignSheet`) gets a "Compare with a shooter" button
  under the Stage picker. Hidden below `md`.
- Pressing it opens a second panel to the **left** of the clip panel, same
  height, the clip panel's width or a little wider. It stays open while the
  user steps clips (arrow keys, Previous / Next, Use this, Skip clip) and
  closes with its own cross or when the clip panel closes. Whether it was
  open is remembered for the session (my call: a `useState` in the page,
  not a pref).
- Top: "Compare with" and a shooter dropdown. It lists the match's shooters
  who have reference footage on at least one stage, default the one with
  the most such stages; the shooter selected in the clip panel is excluded
  only when another exists (comparing Janne with Janne's own earlier clips
  is legitimate: it shows the stages already covered). When nobody has
  reference footage the Compare button is disabled with the title "No
  shooter has footage yet", and the panel never opens empty.
- A player under it, playing the chosen stage's reference video with the
  browser's own controls, muted, independent of the clip player.
- A heading "Stages: N still missing for <shooter being assigned>", then a
  grid of one tile per stage of the match (`SortView.stages`, placeholder
  stages included since the shooter shot them too, three columns): stage number, name, and the reference frame as the tile's
  background. The current tile has an outline. A stage with no reference
  footage is an empty tile that says "no clip". A stage the shooter being
  assigned already has is dimmed and says "<name> has it" instead of the
  stage name. Stage name and "has it" copy follow the `ui-budget.md`
  rules: no coloured fills, `Label` for the heading, numerals with the
  `numeral` utility.
- Clicking a tile plays it. "Use stage NN" on the player sets the clip
  panel's Stage picker to that stage (the same `setStage` path as the
  dropdown). Nothing else changes.
- Changing the shooter in the clip panel re-derives the dimming and the
  count; changing the reference shooter re-derives the tiles.

## Where the reference footage comes from (decided)

For a reference shooter S and stage n, in this order:

1. S's project: the stage's primary video. Its trimmed audit clip when it
   exists, else the raw file. "Exists" is what `export_preview._trim_for`
   decides (the export trim, then `ui.audio.resolve_trim_for_read` with
   its legacy-name fallback); the builder lifts that resolution into a
   shared helper rather than calling `trimmed_primary_path` directly,
   which knows no legacy names and would call old projects untrimmed.
   Played through the existing stream route (`api.videoStreamUrl(slug,
   path, kind, version, stage)` with `"trim"` or `"auto"`), nothing new;
   the reference carries the `video_id` and `version` that route wants,
   since one source file can be registered on several stages with a trim
   each. A raw file that is not reachable (drive unmounted) and has no trim
   is no reference: the tile says "no clip".
2. Otherwise, this sort: the **checked** clip whose proposal is (S, n),
   the primary one when several. Checked means the user has accepted it
   (the engine's confident set is pre-checked, a user override checks
   itself); unchecking a clip withdraws it as a reference, because an
   unchecked clip is one the user doubts. Played through the sort's own
   `footageSortVideoUrl`.

A clip that is both (imported from this sort) is case 1.

The frame for a tile:

- Project trim: the beep's position in the trim plus 2 s, clamped the way
  `_trim_for` clamps it (`min(trim_pre_buffer_seconds, beep_time)`), or
  the trim's start + 2 s with no beep. The user's rule: the stage is
  visible early; a beep is a bonus, not a requirement. `thumbnail.ensure`
  takes no time, so this is a new frame extractor beside it, using the
  same `_publish` discipline (partial file, rename, timeout).
- Project raw file (no trim yet) and sort clips: `thumbnail.ensure`'s
  default pick (1 s, or a tenth of a short clip). For sort clips this is
  the thumbnail the sort already has (served by `/thumbs/{index}.jpg`,
  cached under the source's own name); reuse it, build nothing.

## Server (decided)

Local only, in `ui/footage_sort_api.py`:

- `SortView` gains `references: list[SortReference]`, one per (shooter,
  stage) that has reference footage: `shooter`, `stage`, `source`
  (`"project" | "sort"`), `video_path` (project) or `clip_id` (sort),
  `stream_kind` (`"trim" | "auto"`) and `frame: bool`. Coverage ("has it")
  is not a server field; the SPA derives it, see below.
- `GET /api/match/footage-sort/{scan_id}/reference-frames/{slug}/{stage}.jpg`
  serves the project frame (404 until built; the tile shows a placeholder
  and the page's existing poll picks `frame` turning true). Sort-clip tiles
  use the existing thumb route.
- Project frames are built off the request path, two ways, same code:
  `run_footage_sort_scan`'s background pass builds them after the
  thumbnails and before the strips (a reference frame is worth more than
  a scrub strip to someone who is hunting); and `_view`, when it lists a
  project reference whose frame is missing, submits the one job that
  builds the missing frames for this scan (deduplicated per scan, the
  `_audio_pending` shape) and returns at once. That covers a scan
  reopened later, an import mid-review and a re-trim, none of which
  re-run the scan pass. A request never extracts a frame itself.
- Cache: `<sort dir>/reference/<slug>-stage<N>-<video_id>.jpg`, with the
  trimmed file's mtime in the name or beside it so a re-trim refreshes
  it. Pending count: `_view` computes `frames_pending` from the on-disk
  cache the way it computes `strips_pending`, and the page polls while
  either is above zero.
- The view is rebuilt per request already (`_view`), so an import during
  the review moves a reference from `sort` to `project` on the next poll,
  and the frame follows by the job above.

## SPA (decided)

- `lib/footageSort.ts` gains the pure derivations, with tests:
  `referenceShooters(view)` (who has footage, sorted by count),
  `referenceTiles(view, referenceShooter, assigningShooter)` (one entry per
  match stage: the reference, the frame URL, `has: boolean`), and
  `missingCount`. "Has it" for the assigning shooter A on stage n is: A's
  project has a video on n, or this sort has a checked clip proposed (A, n)
  that is not the clip being assigned. The view already carries
  `ShooterRef.stages`, but that is every stage in the project, not the
  ones with footage; the builder adds `stages_with_footage` (or renames)
  to `ShooterRef` rather than overloading `stages`.
- `components/footage/ReferencePanel.tsx`: the panel. The page owns the
  open state and the reference shooter; the panel owns the chosen stage.
  Rendered as a sibling of the `Sheet`, positioned to its left (the shared
  `components/ui/Sheet` is `md:max-w-[440px]` on the right; the builder
  picks the mechanism, a second fixed panel at `right: 440px` is the
  obvious one, and checks it on a 1280 px window).
- `AssignSheet` gets the Compare button and an `onUseStage` wiring into its
  `setStage`. The sheet always has a shooter selected (the proposal's, else
  the first), so "missing for" always names one.
- Keyboard: the clip panel's keys are unchanged. The reference player
  gets no shortcuts (the clip is what the user is scrubbing).
- Types: export the OpenAPI, `pnpm gen:api`, aliases in `lib/api.ts`.

## Tests

- `tests/test_footage_sort_api.py`: a view lists a project reference for an
  imported stage (trim present: `stream_kind == "trim"`; absent:
  `"auto"`) and a sort reference for a checked clip; after the per-shooter
  import the same stage reads `project`. The frame route 404s before the
  build and serves a JPEG after `run_footage_sort_scan`. Each must fail
  without the feature.
- `lib/footageSort.test.ts`: the three derivations, including "has it"
  from the sort excluding the clip being assigned, and the shooter default.
- `pages/FootageSort.test.tsx`: open the panel, click a tile, press Use
  stage, the Stage picker shows it, Use this sends that stage. The panel
  survives Next.
- What's new entry (`whats-new` skill), Sort footage wording.

## Left to the builder

- Exact panel width and the responsive cut-off, within the rules above.
- Tile placeholder look while a frame builds.
- The frame's cache key and the `video_id` helper to reuse.
