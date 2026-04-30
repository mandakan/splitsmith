# Production UI -- Deferred Vision

This file captures the full UI vision discussed during v1 development. The
audit-only UI ships first as a separate, narrow tool. Everything below is
intentionally deferred and should be filed as a GitHub issue (or split into
several) when the audit UI is in real use and the next bottleneck is clear.

## Vision summary

A desktop-class workflow for an IPSC competitor reviewing a full match's worth
of head-mounted footage. Today the tool is CLI-only with a CSV-cull workflow.
The production UI would handle the full ingest -> trim -> review -> export
loop in one place.

## Proposed feature surface

### Match ingest
- Drop in / point at an SSI Scoreboard JSON export.
- Drop in / point at a folder of raw videos.
- Auto-map videos to stages by file timestamp (already implemented in
  `video_match.py`).
- Manual correction UI for ambiguous / unmatched videos:
  - Drag-and-drop assignment of video to stage.
  - Inline preview / play of each video while assigning.
  - Show the existing match-window heuristic (scorecard time +/- tolerance).

### Multi-angle support
- A single stage can have **N videos** mapped to it (head cam + bay cam +
  competitor's phone, etc.).
- Per stage, the user picks one "primary" video for shot detection; the
  others are anchored to the primary's beep.
- Useful for picture-in-picture (PiP) compositions in FCP, or quad-cam
  side-by-side of multiple competitors on the same stage.

### Beep detection with manual correction
- Run beep detection on the primary video.
- Show the detected beep time + waveform around it.
- Allow user to drag/click to correct.
- Per-stage corrections are saved alongside the per-stage analysis.

### Shot detection with manual correction
- This is the **core audit loop** that the v1 audit UI handles.
- In production: batch view across all stages, jump between stages, see
  match-level statistics (total shots, average splits, etc.).

### Beep-only sync mode
- A "skip shot detection" toggle per stage / per video group.
- Use case 1: Insta360 head cam + iPhone front-facing video composed as PiP
  in FCP -- only need the two clips beep-aligned.
- Use case 2: 4 competitors on the same stage, side-by-side composition --
  4 clips beep-aligned. Scoreboard JSON provides per-competitor data.
- Should produce an FCPXML with the videos pre-aligned on the timeline (no
  shot markers needed).
- Probably easier as its own CLI subcommand (`splitsmith sync`) that
  the production UI calls under the hood.

### Export
- Per stage: trimmed video(s), splits CSV, FCPXML, report -- same as today.
- Match-level: a single FCPXML for the whole match? (Open question.)
- Multi-angle FCPXML: video lanes per camera angle, all beep-anchored.

## Architectural notes

- The CLI keeps being the engine; the UI is a thin orchestration layer that
  shells out (or imports) the existing modules.
- Beep / shot detection are not re-trained from user corrections; the
  user's correction IS the source of truth and overrides detector output for
  that stage. Don't build a feedback loop into the detector.
- Multi-angle sync probably wants its own FCPXML generator path (separate
  from `fcpxml_gen.py` which assumes one asset).

## Why this is deferred

- Two UIs (audit + orchestration) tangled together is a recipe for shipping
  neither. v1 ships the narrow audit tool to validate the SPA approach.
- Multi-angle and beep-only-sync are real but additive features; their
  absence doesn't block the basic IPSC training-analysis workflow.
- Stage-mapping with manual correction is currently clunky-but-tolerable
  (the CLI surfaces unmatched / ambiguous stages and you re-run with cleaner
  inputs). Not the most painful thing right now.
