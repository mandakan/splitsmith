# Sort a footage folder across shooters (v1, local mode)

Status: approved direction 2026-10-01. Builds on #1118 (embedded recording
time, several cameras per run).

## Problem

Club mates hand over a folder each (Google Photos export, Quantum File
Browser): phone clips of the other squad members, sometimes their own clip
filmed on their phone by someone else, head-cam clips, photos. Today the
user sorts these by hand into `<shooter>/<head|hand>` folders, imports each
folder under the right shooter, then fixes stages. The Footage page imports
per shooter only.

## What the data says (2026 matches on X9)

- Every phone and head cam embeds its capture start. Filesystem dates on
  shared files are copy dates (median 4-7 h off, months in two matches).
- Device and folder say nothing about who is in a clip: Martin's phone films
  Mathias and Anton, a club mate holds the shooter's own phone, two shooters
  own the same iPhone model.
- Each competitor's scorecard is typed shortly after their own run. "The
  first scorecard of any squad member typed after the clip started" gave the
  right shooter and stage for 218 of 221 clips whose clock was right; the
  blind run over the unsorted Höstfinalen XI folders sorted all 24 phone
  clips plausibly (Martin's folder: Mathias and Anton; Anton's: Mathias and
  Martin), confirmed by the user.
- Scorecard times are not fully reliable (SSI bugs, scores typed after the
  fact). They are the prior, never silently final.
- Phone clocks are right (network time). The Insta360 head cam is not:
  minutes fast at Höstfinalen, 76 days off at ESS. A clock error belongs to
  the device, not to a shooter, and one device can film two or three shooters.
- Cameras on one run start 0-78 s apart; consecutive squad mates >= ~230 s.

## Scope

In: local mode (desktop app, `splitsmith ui`), one parent folder scanned
recursively, all shooters already added to the match with scorecards
imported, review then import into each shooter's project.

Out (v1): hosted uploads; audio-based run clustering and stage-time
confirmation (later confidence boosters); renaming or moving the source
files; photos (skipped, counted).

## Flow

1. Footage page, local mode, two or more shooters: **Add footage** offers
   "Sort a folder" next to the per-shooter pick. The user picks a
   parent folder.
2. A `footage_sort_scan` job walks it: videos only (existing
   `VIDEO_EXTENSIONS`), skips photos, `.llc`, dotfiles. Per clip one ffprobe:
   embedded start, duration, make/model, plus a thumbnail. Where each file
   is already registered is looked up live on every read (resolved path,
   Unicode-composed: macOS lists names decomposed): on a stage it is done
   and never imported again; unassigned (the per-shooter Add footage files
   everything under the active shooter) it is sorted like a new file and
   moved to the right shooter on import (0.46.1).
3. The proposal engine (pure) assigns each clip a shooter and stage with a
   confidence and a reason, groups runs, and decides per camera whether its
   clock is trusted, fitted, or needs an anchor.
4. Review page. "Needs you" first: per camera without a usable clock, "Which
   run is this?" on one clip (anchor); ambiguous clips. Then the proposals,
   confident ones pre-checked. Leftovers (no shooter) collapsed, unchecked.
5. **Import** registers each checked clip under its shooter (link in place
   or copy, the page's existing toggle) and assigns it: the run's primary
   when the stage has none, otherwise secondary. The user confirmed the
   (shooter, stage), so no time comparison with an existing primary: that
   primary may come from a camera whose clock is minutes off. Beep jobs
   queue through the scan route's hook (``app.state.auto_queue_beep``). A
   report is written.

## Engine: `splitsmith/footage_sort.py` (pure, no I/O)

Input: `list[SortClip]` (path, folder, start or None, duration, make, model,
filename) and `list[ShooterScorecards]` (slug, name, per-stage
`scorecard_updated_at`). Config `FootageSortConfig` in `config.py`.

- **Camera key**: (source subfolder, make/model, filename scheme), with the
  scheme from the name (`IMG_####`, `VID_YYYYMMDD_HHMMSS_##_###`,
  `video-####_singular_display`, else the extension). Two identical iPhones in
  two folders are two cameras.
- **Per-clip candidate**: the first scorecard (any shooter, any stage) with
  `start + lead_min_s < t <= start + duration + tail_max_s` (defaults 10 s,
  8 min). Two different shooters' scorecards within `tie_s` (20 s) of the
  first make the clip ambiguous.
- **Clock trust per camera**: trusted when at least `trust_ratio` (0.8) of its
  clips get a candidate at offset 0 and the start dates fall on the match
  days. Otherwise fit an offset: candidates are `scorecard - start - lead`
  over all pairs, refined on a 15 s grid; the score is the number of clips
  that get a unique candidate with every (shooter, stage) used once. A fit
  is accepted (`fitted`) when the best score covers `trust_ratio` of the
  clips and beats the runner-up by `fit_margin` clips (2); otherwise the
  camera is `needs_anchor`.
- **Anchor**: the user's (shooter, stage) for one clip pins
  `offset = scorecard - start - median_lead`, where median_lead comes from the
  trusted cameras in the same scan (fallback 60 s). The rest of the camera is
  then assigned as for a trusted one; clips still without a candidate stay in
  "needs you".
- **Runs**: clips with the same proposed (shooter, stage) whose corrected
  starts lie within `same_run_seconds` (90, shared with `VideoMatchConfig`)
  form one run. Primary rank head > unknown make > hand (the
  `auto_match` rank). Two runs proposed for one (shooter, stage) are a
  conflict: both go to "needs you" (re-shoot, or one is a neighbour).
- **Confidence**: `high` = trusted clock, unique candidate, no conflict;
  `medium` = fitted or anchored camera; `needs_you` = ambiguous, conflict,
  no candidate on a camera that needs an anchor; `skipped` = no candidate on a
  trusted camera (warm-up, other squad, walkthrough). `high` is pre-checked,
  and so is `medium` on a camera the user anchored (their answer placed
  it); a fitted camera's `medium` waits for a check.
- **Reason**: a short structured record (scorecard shooter/stage/time, gap,
  camera offset, other cameras on the run) the page words; the engine never
  formats prose.

Output: `SortProposal` (Pydantic) with cameras (key, label, clip count,
clock state, offset), clips (proposal, confidence, reason, run id) and runs.
Re-running the engine with anchors and user overrides is cheap and pure;
the API re-runs it on every change, so the page never re-derives the rules.

## Persistence and audit trail

`<match>/footage_sort/<scan_id>.json` holds the scan (probe results), the
user's anchors and overrides, and the last proposal, so the review survives
a reload. Import writes `<scan_id>-report.json`: every clip, the decision,
who decided (engine/user), and the outcome. Local files only, never a
`state_docs` kind (that would enter the sync manifest).

## API: `splitsmith/ui/footage_sort_api.py` (router, local only, 404 hosted)

- `POST /api/match/footage-sort/scan {source_dir}` -> `{job_id, scan_id}`.
- `GET /api/match/footage-sort/{scan_id}` -> the proposal.
- `PUT /api/match/footage-sort/{scan_id}/decisions` `{anchors, overrides,
  checked}` -> re-run engine, return the proposal.
- `POST /api/match/footage-sort/{scan_id}/import` -> per-shooter
  `register_video` + assignment, beep queueing, report; returns the summary.
- Thumbnails through a scan-scoped route over `thumbnail.ensure` with a
  cache under `<match>/footage_sort/thumbs`.

Imports go through each shooter's existing `register_video` /
`assign_video` under the same locking as the scan route; no second
registration path.

## SPA

Route `/match/:id/footage/sort/:scanId`, reached from the Footage page (no
nav row). Derivation in `lib/footageSort.ts` (sections, counts, the
"Import N" total, wording), tested; the page maps it to primitives:
`PageHeader`, `Table` rows with thumbnail, file, camera chip, proposal
(shooter, stage), reason line, checkbox; a row `Menu` to change shooter or
stage or skip; `Sheet` for the clip preview and the anchor question; one
primary button, **Import N clips**. Camera clock state as a `Chip`
("Clock 9 min fast", "Clock unknown"). No new nav row, no flavour copy.

## Tests

- Engine fixtures from real data: `tests/fixtures/footage_sort/<match>.json`
  with the probed clip metadata, every squad shooter's scorecards and the
  user's own assignments as labels (Blacksmith, HFO Masters, Tallmilan, Oden
  Cup, Vads; Höstfinalen XI for the unsorted case). Assert the label rate per
  match and zero wrong `high` proposals.
- Clock cases from the same data: Insta360 at ESS (76 days) needs an
  anchor; one anchor resolves the rest of that camera.
- API: scan job on synthetic media (`tests/synthetic_media.py`) with muxed
  `creation_time`; import registers under two shooters and assigns
  primary/secondary; hosted returns 404.
- SPA: `lib/footageSort.test.ts` and a page test with the API mocked.

## Per-shooter import in a multi-shooter match (0.46.1)

Add footage still files a folder under the active shooter, but with two or
more shooters holding scorecards its auto-assign keeps only clips the
engine, over everyone's scorecards, puts on that shooter: a squad mate's
run minutes earlier sits in the active shooter's window (Anton's glasses
became Mathias's primaries in a replay of Höstfinalen XI). The import
banner and the Unassigned panel offer "Sort across shooters", which opens
the sort one folder above the last one added.

## The standard way to add footage (0.48)

In local mode, **Add footage** is the sort whenever any shooter of the
match has scorecards, with one shooter or many: the picker takes a folder
(every video below it) or picked files, and always leads to the review.
A match without scorecards keeps the per-shooter import, and the drop zone
says why. The separate "Sort a folder" button is gone; "Sort across
shooters" stays on the Unassigned panel for clips imported unsorted.

A sort is resumable: Footage lists the ones still open (scanning, or
ready with clips not yet on a stage) with Continue and Discard; discard
keeps the record (status ``discarded``) for the audit trail. A full import
returns to Footage with a one-line summary; a per-shooter import keeps the
review open. A sort closes only when no clip is left that wants a decision
(not on a stage, not skipped): a camera still waiting for its anchor keeps
it open, offered on Footage with what is left (0.48.1).

## Open after v1

Hosted (probe after upload); audio cross-correlation to confirm runs and
fix clocks between cameras; stage time (beep to last shot vs official time)
as a stage check independent of scorecard timing.
