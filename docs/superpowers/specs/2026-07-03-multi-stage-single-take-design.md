# Multi-stage single-take video handling

Date: 2026-07-03
Status: approved (backend sections approved explicitly; ingest UX, take
overview, error handling, and testing sections finalized under delegated
authority in the same session)

## Problem

The user sometimes records several stage runs in one continuous video file
(head-cam left rolling across 2+ stages, occasionally most of a match).
Today splitsmith assumes one primary video per stage with one beep: the
beep detector scans a whole file and returns the single best beep, and
timestamp matching binds a file to exactly one stage. Handling a
single-take file requires cutting it outside splitsmith first. That
pre-processing step must go away.

Footage shape varies: sometimes one stage per file, sometimes 2-4 stages,
sometimes most of a match (30-90 min with walking, resets, and
neighboring-bay timer beeps between runs). The design must treat
one-file-one-stage as the N=1 case of the same flow, not a separate path.

## What already exists

- `RawVideo.covers_stages` (`src/splitsmith/ui/project.py`) models one
  source file covering N stages; `attach_raw_video` merges N:1
  StageVideo-to-RawVideo.
- The hosted attach endpoint (`POST /api/shooters/{slug}/raw-videos/attach`)
  already creates one `StageVideo` per covered stage, all pointing at the
  same source path.
- `trim_video` cuts with absolute offsets into any source, so N stages
  sharing one file trim correctly once each `StageVideo` carries its own
  `beep_time`.

## Gaps this design closes

1. Beep detection is single-shot with no search window.
2. Timestamp matching is greedy 1:1 and cannot split a file across stages.
3. `video_id` is a hash of the path, so N StageVideos sharing a source
   collide.

## Decisions (from ideation)

- Upfront input: the user declares which stages a file covers at attach
  time. No fully-automatic coverage inference as the primary path, no
  mandatory manual scrubbing.
- Beep-to-stage mapping: scoreboard timestamps (`scorecard_updated_at`)
  derive shooting order and approximate positions; declared order is the
  fallback when scoreboard data is absent.
- Review: a new clip-level "take overview" for sanity-checking the
  carve-up; per-stage beep and shot review downstream is unchanged.
- Detection architecture: windowed per-stage detection (approach A). The
  existing single-beep detector runs inside a per-stage search window.
  No new detection primitive, no global multi-beep scan.

## Design

### 1. Data model

- **`video_id` disambiguation.** Today `StageVideo.video_id` is
  `blake2s(str(path))[:12]`. New: assigned videos hash
  `f"{path}#{stage_number}"`; unassigned videos keep the path-only hash.
  IDs are computed fields, not stored, so there is no data migration.
  Implementation must audit every place a `video_id` is persisted or used
  as a cache key (audit JSONs, cache filenames, SPA route params) and
  either fix it or accept the rename. Pre-production, no compatibility
  shims: pick the correct scheme and update all call sites.
- **Search window on `StageVideo`:**
  - `beep_window: tuple[float, float] | None` - seconds into the source
    file that detection searched (None = whole file, today's behavior).
  - `beep_window_source: Literal["scoreboard", "sequential", "manual"] | None`
  Persisting the window serves the audit trail and lets the take overview
  render what detection actually looked at.

### 2. Window derivation (`beep_windows.py`, pure module)

Pure functions: inputs in, windows out, no file I/O beyond what the caller
supplies.

Inputs:
- File wall-clock anchor: ffprobe `creation_time` if present, else
  mtime minus duration.
- Per covered stage: `scorecard_updated_at`, `time_seconds`.
- `BeepWindowConfig` (new Pydantic model in `config.py`, YAML-overridable):
  pre-pad, post-pad, reset margin, minimum window length. Defaults sized
  for scorecard approval landing 1-3 min after the run: expected beep
  offset = (scorecard_updated_at - video_start) - stage_time - lead;
  window = expected +/- ~3 min, clamped to file bounds.

Modes:
- **Scoreboard**: one window per covered stage from the math above.
  Windows may overlap; overlap alone is not an error.
- **Sequential fallback** (no scoreboard timestamps): declared coverage
  order is shooting order. Stage 1 searches from file start; stage i+1
  searches from `beep_i + stage_time_i + reset_margin` to end of file.
  Each completed detection narrows the next window, so jobs for one file
  run in declared order rather than fully parallel in this mode.
- **Manual**: the user drags a window in the take overview; re-run uses
  exactly that range.

Conflict rule: if two stages resolve to beeps within 2 s of each other,
both are flagged as conflicted (neither silently wins).

### 3. Detection: job layer only, detector untouched

`detect_beep(audio, sample_rate, config)` stays as-is; calibration and
existing fixtures stand. The per-video job
(`_run_detect_beep_for_video`) changes:

1. Compute or read the stage's `beep_window`.
2. Extract only that window's audio via ffmpeg `-ss/-t` to a WAV.
3. Run the existing detector on the slice.
4. Add the window start offset back onto `beep_time` and every candidate
   before persisting.

Job granularity is unchanged: one `detect_beep` job per (stage, video),
same chaining into trim then shot_detect. Attaching a file with
`covers_stages=[...]` enqueues one windowed job per covered stage.
A single-stage file gets window = whole file and behaves exactly as today.

Neighboring-bay false positives are suppressed structurally: beeps outside
the stage's window are never seen by the detector.

### 4. Ingest UX (hosted and local)

- **Coverage declaration**: the add-footage flow gains a stage-coverage
  multi-select. Both paths (hosted R2 upload + attach, local scan +
  assign) funnel into one shared code path that creates N StageVideos,
  records `covers_stages`, and enqueues windowed detect jobs.
- **Server-side suggestion**: when duration + wall-clock anchor are
  available, the server proposes coverage by intersecting the file's
  wall-clock span with the stages' `scorecard_updated_at`. The suggestion
  pre-fills the multi-select; the user confirms or edits. Files whose
  span covers several scorecard timestamps stop being marked "ambiguous"
  by auto-match and instead carry a multi-coverage suggestion.
- Declaring a single stage keeps today's behavior and UI weight: the
  multi-select defaults to the suggested single stage.

### 5. Take overview (clip-level review)

New SPA view scoped to a raw video, reachable from the ingest clip detail
and from any stage page whose primary video is part of a take ("part of
take X" link).

Contents:
- Full-file envelope waveform. New endpoint
  `GET .../raw-videos/{key}/peaks` returns a downsampled envelope
  (~2000-4000 peaks) computed via ffmpeg and cached next to the audio
  cache; generated on first request or as part of the first detect job
  for the file.
- Per-stage shaded search windows with stage labels, detected beep
  markers with confidence, and status per stage: found / none / conflict.
- Actions: drag a window's edges and re-run detection for that stage
  (window becomes `manual`); edit coverage (add/remove a stage, which
  creates/removes the StageVideo and its jobs); jump to the per-stage
  beep review for fine work.

Non-goals: the overview does not pick beep candidates and has no
persisted "confirmed" state. `beep_reviewed` on each StageVideo remains
the single source of truth, set through the existing per-stage review.
The overview is a lens and gross-error fixer, not a second status system.

### 6. Error handling

- **Empty window** (muffled beep, camera paused, wrong coverage): stage
  shows "no beep found" in the overview; user drags/widens the window and
  re-runs, or sets the beep manually via the existing per-stage manual
  set.
- **Conflict** (two stages latched onto beeps within 2 s): both flagged
  in the overview; user adjusts one window and re-runs.
- **Wrong coverage**: user removes the stage from coverage in the
  overview; the StageVideo and its pending jobs are deleted.
- **Camera clock skew**: windows are systematically shifted; the overview
  makes this visually obvious (markers hugging window edges or missing).
  A global "shift all windows" nudge is deferred; noted as a future
  improvement, not built now.

### 7. Testing

- **Window derivation**: pure unit tests, no audio. Scoreboard mode,
  sequential fallback, clamping, overlap, conflict rule.
- **Windowed detection**: reuse existing real fixtures. Run detection
  with a sub-window that includes the labeled beep (assert detection +
  correct offset math) and one that excludes it (assert no beep). No
  fabricated fixtures.
- **Job orchestration**: mocked ffmpeg per testing rules; assert the
  slice extraction args and the offset addition on results.
- **Attach flow**: N StageVideos created, N jobs enqueued, video_id
  uniqueness across stages sharing a path.
- **SPA**: no test runner exists; verify via typecheck + build + scoped
  eslint, plus a bounded headless screenshot of the take overview.

### 8. SPEC.md

Update the pipeline section: one source file may cover N stages; beep
detection runs per (stage, video) inside a derived search window; document
`beep_window`/`beep_window_source` and the take overview in the module
responsibilities.

## Out of scope

- Global multi-beep scan (approach B) and automatic candidate rescue for
  empty windows (approach C) - manual window adjustment covers the
  failure mode; revisit if empty windows prove common.
- Global clock-skew nudge control.
- Compare/export changes: downstream consumes per-stage trims, which are
  unaffected.
