# UX redesign -- capability inventory

A flat list of what Splitsmith can do today, decoupled from where in the
UI it currently lives. Built from a survey of pages, components, CLI
commands, FastAPI endpoints, and scripts.

**Used forwards** as a loose check: any new IA we propose should be able
to host the capabilities its surfaces need to host. **Used backwards** as
a strict check: at the end of the redesign, every capability must have a
home or be explicitly retired.

Markers:
- `[CLI]` -- CLI-only today, no UI surface
- `[script]` -- script-only today, no UI surface
- `[stub?]` -- looks half-finished or unused

## Project / match management

- List, filter, switch, and pick a project from a project picker
- Bind / forget recent projects (kept as a history list)
- Create a new project at a specified path with scaffolding
- Open an existing project by path (auto-detects project.json)
- Display project metadata: name, root path, schema version
- Archive (export) a project as a `.tar.gz` backup, selectively including `trimmed/`, `exports/`, `raw/`, `audio/`
- Restore a project from a `.tar.gz` archive, with optional overwrite
- Forget a recent project from history
- Cleanup orphaned directories (`trimmed/`, `exports/`) for deleted stages

## Footage ingest

- Scan a folder of video files and symlink into `raw/`
- Auto-match videos to stages by mtime
- Assign / reassign videos: primary, secondary, ignored
- Unassign videos from a stage
- Remove videos from the project (and disk)
- View per-video link status across stages
- Relink videos to different stages or roles (relink dialog with folder picker)
- Select camera model and mount type with provenance tracking

## Beep handling

- Auto-detect beep time on one video or across a stage
- View beep candidates ranked by confidence with preview clips
- Manually place / override beep time
- Snap beep to nearest detected peak
- Pick a beep from the candidate list
- Multi-camera beep review (primary + secondary synchronized at detected beep)
- Re-run beep detection with fresh config
- Suppress auto-detection (env var)
- View beep waveform preview with confidence score

## Stage metadata

- Enter stage duration manually (used when beep-to-last-shot is not derivable)
- Specify expected shot count per stage
- Choose / edit target types (pistol, rifle, shotgun, other)
- View stage time section with beep timestamp and recorded duration

## Shot detection and audit

- Auto-detect shots in a single stage or all unaudited stages
- View shot candidates with confidence scores
- Audit on waveform: accept candidate, reject candidate, manually add a shot
- Manually nudge a shot timestamp
- Toggle marker visibility per kind (detected, rejected, manual)
- Undo audit changes (per-session, survives navigation)
- Delete rejected or manual shots
- Auto-advance to next marker after a toggle (FCP-style)
- Save audit work (writes shots + audit events to JSON with session tracking)
- Loop playback within a shot window
- Grid / side-by-side playback for multi-camera audit
- View anomalies (unusual splits, gaps)
- Stepper for sequential shot review with confidence badges
- Keyboard shortcuts for arrow navigation, toggle, save, undo
- Re-run detection on a stage without config changes
- Display list of rejected / deleted / manual-added candidates with reasons

## Multi-camera per shooter

- Each shooter can have multiple videos per stage with roles (primary, secondary, ignored)
- Synchronized multi-camera playback aligned at the beep
- Secondary beep offsets configurable for correct sync
- Trim per camera at export time
- Multi-angle coach view for form critique

## Performance analysis / coach

- View shot splits with color-coded intervals (good <=0.25s, ok 0.25-0.35s, slow >0.35s)
- Per-shot breakdown: shot number, split, interval classification (reload, transition, draw, fire)
- Reclassify intervals manually
- Per-stage and match-wide interval distributions (histograms)
- Coaching notes / improvement flags per shot (stored in audit JSON)
- Synchronized multi-camera playback at each shot

## Multi-shooter compare

- Export multi-shooter compare FCPXML from a manifest YAML `[CLI]`
- Manifest specifies projects + shooter labels to include `[CLI]`

## Export

- Per-stage FCPXML (with shot markers)
- Per-stage CSV (splits + metadata)
- Per-stage text report
- Match-wide FCPXML (multi-stage batch)
- Configure trim padding (Full / Action / Highlight / custom)
- Select which stages to include
- Select output codec for trim re-encode
- View export status per stage (audited / trimmed / exported / not-started)
- Optional overlay rendering (infrastructure present, surface unclear) `[stub?]`
- Reveal exported files in the OS file browser
- Handle source-unreachable errors (missing raw footage)

## Fixture / corpus / training

- Ingest fixture JSON with embedded audit (shots + timing)
- Edit fixtures via the Review page (no project context)
- View fixture audit (shares components with project audit)
- Save edited fixtures back to disk
- List fixtures in catalog (Lab)
- Batch eval: run CLAP + PANN + GBDT feature extraction across fixtures
- Live tuning: adjust ensemble consensus + voter weights and re-score
- Multi-fixture compare / diff overlay (Lab)
- Promote secondary candidates to verified shots within a fixture
- Queue HITL tasks: review candidates flagged by ensemble thresholds
- Promote candidates against an anchor fixture (confirm, nudge, escalate)
- Validate / audit promoted fixtures
- Retrain ensemble: build calibration artifacts from audited fixtures
- Tag fixtures with labels
- Search / filter fixture catalog by slug or label
- Per-fixture metrics (F1, recall, precision) and distributions

## Fixture / training scripts `[script]`

- `build_ensemble_artifacts.py` -- production ensemble calibration + trained GBDT
- `build_ensemble_fixture.py` -- review-time ensemble fixture from raw audio/video
- `build_beep_calibration.py` -- per-camera beep detection thresholds
- `extract_clap_features.py` -- CLAP embeddings
- `extract_audio_embeddings.py` -- PANN embeddings
- `train_classifier.py` -- train GBDT on hand-labeled negatives
- `eval_ensemble.py`, `eval_detector.py`, `eval_beep_detector.py` -- benchmark suites
- `refresh_candidates.py` -- re-run detection on a fixture
- `run_sweep.py` -- grid search over ensemble config

## Background jobs

- Track long-running jobs with progress UI (detection, trim, export, beep scan)
- Cancel in-progress jobs
- Acknowledge job failures and clear them from queue
- Stream job progress in real time (status, eta, errors)
- Queue multiple jobs (serial or parallel)

## Scoreboard integration

- Import SSI Scoreboard JSON
- Fetch shooter data and search competitors
- Refresh match times from scoreboard

## Settings, provenance, help

- Theme: light / dark / system
- Setting provenance (where a value came from: default, user, auto, fixture)
- Help overlay with keyboard shortcuts and UI guidance
- User identity: recent projects, scoreboard identity (shooter name, club)
- Settings panel: automation, camera model, mount type

## CLI surface

- `single`, `detect`, `process`, `review`, `ui`, `audit-prep`, `audit-apply`, `fcpxml`, `overlay`, `clean`
- `project export`, `project import`
- `compare export`
- `mcp` -- expose Splitsmith as an MCP tool
- `lab` -- ensemble tuning sub-app

## Misc

- Design system page (color tokens, typography, components, a11y checklist) `[stub?]`
- Filesystem browser endpoint (lists / probes directories for path pickers)
- Thumbnail generation and caching for video previews
- Match-level export overview (stages complete, export ready)
- Anomaly detection (splits outside expected distribution)

---

## Coverage notes

- **Multi-camera per shooter** is woven throughout the existing code -- secondary video sync, per-camera trim, secondary beep offsets, multi-angle coach views. The IA must explicitly support this; it is not optional and not new work.
- **Export overlays** are infrastructure-present but UI-absent today. Confirms JTBD job #1a is a gap, not a missing connection.
- **Multi-shooter compare** is CLI-only today. UI is the gap.
- **Lab** is a fully featured ensemble-tuning interface, not a sandbox. Retiring it requires moving real functionality out, not just deleting a page.
- **Design.tsx** appears to be a stub / spec page rather than user workflow.
