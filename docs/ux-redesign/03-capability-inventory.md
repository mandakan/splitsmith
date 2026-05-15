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
- Create a new match (replaces "create project") -- scaffolds the match
  folder + a primary shooter under `shooters/<slug>/`
- Open an existing match or legacy single-shooter project by path (auto-
  detects `match.json` vs `project.json`, routes to first shooter when
  pointed at a match folder)
- Display project metadata: name, root path, schema version, kind
  (`match` / `legacy` / `missing` / `unknown`)
- Archive (export) a project as a `.tar.gz` backup, selectively including `trimmed/`, `exports/`, `raw/`, `audio/`
- Restore a project from a `.tar.gz` archive, with optional overwrite
- Forget a recent project from history
- Cleanup orphaned directories (`trimmed/`, `exports/`) for deleted stages
- **Merge N legacy single-shooter projects into one match folder** -- CLI
  (`splitsmith match merge`) and SPA wizard (`/pick/merge`). Validates
  scoreboard + stage agreement, surfaces conflicts before writing, binds
  the first shooter on completion.

## Match-as-object data model

- Each match is a folder containing `match.json` + `shooters/<slug>/`
  subdirs; the per-shooter dir holds a `project.json` (legacy shape
  preserved for audit/ingest endpoints)
- Add, remove, and switch the active shooter on a match
- List shooters on the active match with per-shooter audit progress,
  camera coverage, and HITL queue counts
- Per-shooter video streaming for cross-shooter views (Compare)
- Shooter slug stable across renames; per-shooter racing-color identity
  used in Compare + Shooters management

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
- **Cross-shooter beep queue** -- two-pane review surface that groups
  every shooter's per-stage beeps into one list with status dots
  (confirmed / pending / current / missing), keyboard-driven advance,
  alt-candidate panel, mini-waveform detail

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
- **Playhead-driven sync** -- the active shot, ruler dot, and shot list
  row all advance automatically as the video plays; list auto-scrolls
  the active row into view (smooth, block:'nearest'). Clicking a shot
  still seeks; once playback resumes the auto-advance takes over.
- Match-wide stat cards (match time, avg split, fastest, slowest)
- Per-stage breakdown table with 4-color split-distribution mini-bars
- CRT split histogram with median line + practice priorities (P1/P2/P3)
- Annotations feed aggregated across all stages

## Multi-shooter compare

- Export multi-shooter compare FCPXML from a manifest YAML `[CLI]`
- Manifest specifies projects + shooter labels to include `[CLI]`
- **Multi-shooter sync timeline (UI)** -- watch N shooters' runs played
  in sync at the beep with master/slave video sync, layout toggle
  (2x2 / 1x4 / Stack), per-shooter visibility chips, audio-source
  LED ring, F1-style timeline with per-shooter tracks, beep marker,
  shot dots, end-of-run total times, ranking table

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
- **Jobs drawer (universal)** -- bottom-right FAB that glows when work
  is running or failed, opens a slide-out with Running / Needs attention
  / Queued / Completed groups, worker pool chip, acknowledge-all action.
  Mounted on every project-bound shell.

## Mode separation (Match / Developer)

- Global mode state persisted in localStorage; sets `data-mode` on the
  document root; flips the accent token from LED red to cyan
- Mode toggle in every shell header (segmented control)
- **MatchShell** -- per-match sidebar + Shot Timer header for shooter-
  facing surfaces (Overview / Audit / Coach / Shooters / Beep review /
  Compare / Export)
- **DeveloperShell** -- cyan-accented workflow stepper sidebar with
  Corpus / Review queue / Validate / Retrain steps, plus a "Tools"
  section that hosts legacy Lab + fixture review under "Legacy" pills
- Active-ensemble model chip in the dev shell header (version, recall,
  fixture count)
- Mode-aware focus ring (red in match mode, cyan in dev mode)

## Developer workflow surfaces

- **Corpus** -- fixtures table with filters + tag taxonomy + recall
  bars; promotion inbox card; workflow status banner across the 4 steps
- **Review queue** -- three-column (sidebar + queue list + detail);
  routes the user into `/review?fixture=...` for the actual edit;
  promotion items from match mode land here automatically
- **Validate** -- run-config bar (split strategy, corpus, consensus
  slider, apriori toggle), headline metrics, per-shooter holdout panel
  as centerpiece, per-venue breakdown, confusion matrix, voter
  decomposition
- **Retrain** -- compare strip (shipped vs candidate), 6-stage pipeline
  with motherboard trace + running pulse, CRT log tail, before/after
  metric rows, per-voter detail (A/B/C), build-history table

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

- **Multi-camera per shooter** is woven throughout the existing code -- secondary video sync, per-camera trim, secondary beep offsets, multi-angle coach views. The IA explicitly supports this; not optional.
- **Export overlays** are infrastructure-present but UI-absent today. Confirms JTBD job #1a is a gap, not a missing connection.
- **Multi-shooter compare in UI** -- delivered (#328). The legacy `[CLI]` lines for `compare export` remain as the export path; the UI surface is the new sync timeline.
- **Lab** has been retired into Developer mode (#331). Review functionality lives under `/dev/review`; the Lab catalog is still reachable under `/dev/legacy/lab` with a "Legacy" pill, pending a removal milestone after parity is verified.
- **Design.tsx** -- still a spec page, now under `/_design` only.
