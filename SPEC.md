# Technical Specification

This document is the source of truth for the splitsmith implementation. Read this before writing any code. If something is ambiguous, ask before guessing.

## Goals

1. Take head-mounted camera footage of an IPSC stage and produce per-shot split times accurate to within ~10ms.
2. Produce a Final Cut Pro timeline with a running timer overlay and per-shot split labels, ready to open and review.
3. Be robust enough to batch-process a full match (7–10 stages) in one command.
4. Surface uncertainty rather than hide it — flag anomalies for manual review.

## Non-goals

- Real-time / live analysis. This is offline batch processing.
- GUI. CLI only.
- Cloud / API hosting. Local tool.
- Distinguishing the user's shots from neighbouring bays' shots. Best-effort only; manual review will catch these.
- Classifying split semantics (draw vs split vs transition vs reload). The user does this; the tool surfaces the data.
- Cross-platform GUI tooling. macOS-first; Linux best-effort.

## Architecture

### Pipeline

```
raw videos + stage JSON
    │
    ▼
[1] Match videos to stages         (video_match.py)
    │   uses video mtime/ctime vs scorecard_updated_at
    ▼
[1b] Optionally: declare take coverage          (api: PATCH shooters/{slug}/raw-videos/coverage)
    │   one source file may cover N stages;
    │   POST suggest-coverage proposes stages from client-probed span;
    │   attach with covers_stages creates N StageVideos + enqueues
    ▼
[2] Derive per-stage search window               (beep_windows.py)
    │   scoreboard mode: anchor on scorecard_updated_at;
    │   sequential fallback: previous stage's beep + time + reset_margin;
    │   conflict detection: beep_windows.find_beep_conflicts flags
    │   stages within conflict_threshold_s (default 2 s)
    ▼
[3] Detect start beep per (stage, video)        (beep_detect.py, jobs)
    │   inside its derived search window via ffmpeg -ss/-t;
    │   bandpass + tonal/duration scoring + calibrated confidence;
    │   automation.beep_low_confidence_threshold gates auto-trust
    │   -> below it, items land in `GET /api/hitl-queue` (#219);
    │   windowed mode soft-fails (beep_auto_detect_failed) vs raising;
    │   sequential chain: next covered stage resumes from this beep
    ▼
[4] Trim video                     (trim.py)
    │   lossless (`-c copy`) for archival; audit-mode short-GOP
    │   re-encode for SPA scrub
    ▼
[5] Detect shots                   (shot_detect.py / ensemble/)
    │   3-voter ensemble (envelope + CLAP + GBDT; PANN folded into
    │   GBDT as a feature) with per-camera-class thresholds +
    │   adaptive priors from stage_rounds; consensus seeds shots[]
    ▼
[6] Generate outputs               (csv_gen.py, fcpxml_gen.py,
    │                              ui/exports.py, ui/match_exports.py,
    │                              report.py)
    │
    ▼
CSV + FCPXML + report per stage   match-level FCPXML / MP4 /
                                  YouTube sidecar;
                                  take overview + peaks endpoints
```

The CLI walks the pipeline in batch (`splitsmith process`); the
production UI runs each stage on demand and surfaces the HITL queue
+ confidence gates; the MCP server (`splitsmith mcp`, issue #211)
exposes every step as agent-callable tools so a Claude Code skill
or other MCP client can drive the same flow.

### Module responsibilities

**`cli.py`** — Typer-based CLI. Two main commands: `process` (batch, takes stage JSON) and `single` (one video, explicit time). Handles output directory creation and orchestration. No analysis logic here.

**`video_match.py`** — Match raw video files to stages. Strategy:
1. Read video file creation/modification time (prefer ctime where available).
2. Compare against `scorecard_updated_at` from stage JSON, with configurable tolerance (default ±15 minutes).
3. If multiple videos match a stage or none match, prompt for manual mapping.
4. Output: `dict[stage_number, video_path]`.

Be aware: `scorecard_updated_at` is when the score was *typed in*, not when the stage was shot. Real shoot time is typically 1–10 minutes earlier. Bias the matching window accordingly.

**`beep_windows.py`** - Pure module deriving per-stage search windows inside multi-stage single-take videos.
- `derive_scoreboard_windows`: for stages with `scorecard_updated_at`, expected beep offset is `(scorecard - video_start) - stage_time - scorecard_lead_s`; pads by pre/post; clamps to file bounds.
- `sequential_window`: fallback when no scorecard timestamps; previous stage's beep anchors the next search window; each stage runs to end-of-file so found beeps narrow downstream searches, never current.
- `find_beep_conflicts`: flags stage pairs whose detected beeps sit closer than `conflict_threshold_s` (default 2 s); signals carve-up errors where two stages latched onto the same physical beep.
- All functions pure: datetimes + seconds in, windows out. No file I/O, no project access - the job layer (server.py) resolves the video's wall-clock start, duration, and sibling beeps, then calls in here.
- Configuration: `BeepWindowConfig` in config.py (scorecard_lead_s, pre/post_pad_s, reset_margin_s, min_window_s, conflict_threshold_s).

**`beep_detect.py`** — Find the start beep timestamp in the audio.
- Most shot timer beeps are pure tones in the 2.2-3.3 kHz range, lasting 200-500 ms (empirically 2298, 2402, 2698, 2700, 3198 Hz on the labelled fixture set).
- Approach: bandpass filter to 2-5 kHz, compute Hilbert envelope, smooth at 40 ms; rank candidate runs by `silence_score * tonal_factor * duration_factor`.
- Adaptive cutoff: `max(min_amplitude * peak, noise_floor * noise_factor, min_abs_peak)`. The noise-floor leg recovers handheld / phone clips where the beep is faint in absolute terms but well above the recording's median noise floor.
- Return: a `BeepDetection` with `time` (rise-foot leading edge), `peak_amplitude`, `duration_ms`, calibrated `confidence` in [0, 1], and the ranked candidate list.
- The confidence formula (in `candidate_confidence`) blends tonal purity, duration plausibility, and saturating silence preference, tilted by the margin to the runner-up. Empirically validated against `tests/fixtures/beep_calibration/`: confidence >= 0.7 is right ~95 % of the time. The HTTP server / MCP / SPA use this against `automation.beep_low_confidence_threshold` (default 0.6) to decide whether the auto-trust chain fires (#219).
- Multi-stage mode: receives a derived `beep_window` tuple (start_s, end_s) from beep_windows.py; ffmpeg extracts that span's audio via `-ss start_s -t (end_s - start_s)`; detection runs inside the window; results are offset back to source-absolute via the window bounds. Windowed mode soft-fails (sets `beep_auto_detect_failed = True`) instead of raising when no candidate clears the confidence threshold, allowing sequential chaining to continue to the next covered stage.
- Must be robust to: ambient match noise, RO commands, distant beeps from other bays, AGC'd handheld phones with faint beeps, mid-stage shots with quiet pre-roll.
- Calibration suite + harness live under `tests/fixtures/beep_calibration/` and `scripts/eval_beep_detector.py`. `top-N` recall + per-confidence-bin precision are pinned in `baseline.json`; layer-2 detector tweaks must keep the auto-trust band at >= 95 %.

**`shot_detect.py`** — Detect gunshot timestamps in audio.
- Use `librosa.onset.onset_detect` with spectral flux as the onset envelope.
- Constrain search to `[beep_time, beep_time + stage_time + 1.0]` window.
- Apply minimum gap between shots (default 80ms — tighter than this is rare even for fast shooters and likely indicates double-detection of a single shot).
- Return: list of timestamps (seconds, relative to audio start).
- Output structure: `list[dict]` with `time`, `confidence`, `peak_amplitude` per shot.

Tuning notes:
- Echoes from steel/walls: typically 10–80ms after shot, lower amplitude. The min-gap parameter handles most of these.
- AGC ducking on Insta360 Go 3S: follow-up shots may have lower amplitude. Don't filter on absolute amplitude; use relative.
- Open guns / heavy comp: shots blend. May need to lower the onset detection delta parameter.

**`trim.py`** — Video trim via ffmpeg subprocess. Two modes (issue #16):
- `lossless` (CLI default): `ffmpeg -ss <start> -i <input> -t <duration> -c copy <output>`. Instant, archival, but inherits source GOP (1-4s on Insta360 head-cam).
- `audit` (UI-screen default for #15): re-encodes with `libx264 -preset fast -crf 20 -g 15 -keyint_min 15 -sc_threshold 0 -pix_fmt yuv420p -c:a copy`. 0.5s keyframe spacing at 30fps so browser scrubbing lands within ~1 frame of the pointer. Audio is stream-copied so the detector input is bit-exact across modes.
- Note: `-ss` before `-i` is fast but may not be frame-accurate; the buffer absorbs any seek imprecision. In audit mode the re-encode re-aligns frames, so the concern is moot.
- Start: `max(0, beep_time - 5)`
- End: `beep_time + stage_time + 5`

**`csv_gen.py`** — Write splits CSV.
- Columns: `shot_number, time_from_start, split, peak_amplitude, confidence, notes`
- Time format: seconds with 3 decimal places (millisecond precision).
- Notes column starts blank; user fills in (`draw`, `reload`, etc.).

**`fcpxml_gen.py`** — Generate FCP timeline XML.
- Target FCPXML version 1.10 (FCP 11.x compatible).
- Structure: project → sequence → spine with three video lanes:
  - V1: trimmed video
  - V2: running timer (basic title with time-of-day expression starting at beep frame)
  - V3: per-shot split titles (~0.5s duration each, monospace, color-coded)
- Color coding rule:
  - Green (`#00C853`): split ≤ 0.25s
  - Yellow (`#FFD600`): split 0.25–0.35s
  - Red (`#D50000`): split > 0.35s
  - Special: first shot (draw) and shots after >1s gap (transitions/reloads) get a different color (e.g., blue) to indicate they're not pure splits.
- Frame rate: detect from source video via ffprobe, generate fcpxml at matching rate.
- Add markers on the V1 clip at each shot timestamp for keyboard navigation in FCP.

**`audio.py`** - Audio extraction and caching for multi-stage clips.
- `ensure_video_audio(...)`: Extract a stage's audio at 48 kHz mono; keyed per-stage per-video so reassignments don't reuse stale caches.
- `take_audio_path(...)` and `ensure_take_audio(...)`: Extract a whole-take audio at 8 kHz mono (lightweight); keyed by blake2s(storage_path) so it cannot collide with per-stage 48 kHz WAVs. Reuses the same extract/cache/storage-push pattern as per-stage audio.
- Peaks JSON (3000 bins): generated post-beep-detection for both per-stage (48 kHz) and whole-take (8 kHz) audio. Filenames encode the bin count.

**`ui/server.py`** - Multi-stage take overview and coverage management (issue #348 / #427).
- Take overview: Per-raw-video endpoints (`GET /api/shooters/{slug}/raw-videos/overview` and `GET /api/shooters/{slug}/raw-videos/peaks`) expose duration, per-stage beep status (found/none/pending), conflict list via `beep_windows.find_beep_conflicts()`, and peaks data.
- Coverage management: `PATCH /api/shooters/{slug}/raw-videos/coverage` edits the covers_stages list; `POST /api/shooters/{slug}/videos/suggest-coverage` proposes stages from a client-probed wall-clock span.
- When coverage is declared (attach with covers_stages, or edit via PATCH), the backend creates N StageVideos (one per covered stage) and enqueues windowed beep-detection jobs for each. Conflict detection flags carve-up errors for the take overview page.

**`splitsmith.mcp`** -- Model Context Protocol server (issue #211).
- Wraps splitsmith's pipeline as agent-callable tools so MCP-aware clients (Claude Desktop, Claude Code, IDE plugins) can drive a match end-to-end.
- Stateless: every tool takes `project_root` (path string) as its first arg so multiple agents can collaborate on the same project without an in-memory handle.
- Optional sandbox: `SPLITSMITH_MCP_ALLOWED_ROOT` (or `splitsmith mcp --allowed-root`) constrains every path argument.
- Tool surface (16 tools across read-only / write / detect / export categories) -- see the README for the full list. Detection tools (`detect_beep`, `detect_shots`, `trim_audit_clip`) are synchronous; `detect_shots` lazy-loads the CLAP/GBDT/PANN runtime once per server lifetime (mirror of the HTTP server's `_get_ensemble_runtime`).
- Companion skill: `skills/splitsmith-match/SKILL.md` is the Claude Code runbook that orchestrates the tools with HITL checkpoints (ambiguous video assignments, low-confidence beeps via `get_hitl_queue`, low-confidence shots).

**`report.py`** — Human-readable per-stage summary.
Format example:
```
Stage 3 — "Per told me to do it!"
Official time: 14.74s
Detected beep at: 12.453s
Detected last shot at: 27.182s (14.729s after beep) — matches official within 11ms ✓
Detected 16 shots.

Splits:
  Shot  1 (draw):       1.42s  ⚠ slow draw
  Shot  2:              0.21s  ✓
  Shot  3:              0.19s  ✓
  Shot  4 (transition): 1.34s
  ...

Anomalies:
  None.

Files:
  Video:  analysis/stage3_per-told-me-to-do-it_trimmed.mp4
  CSV:    analysis/stage3_per-told-me-to-do-it_splits.csv
  FCPXML: analysis/stage3_per-told-me-to-do-it.fcpxml
```

Anomaly detection:
- Beep-to-last-shot window differs from official `time_seconds` by >500ms → flag.
- Any split <80ms → flag (likely double-detection).
- Any split >3s within the stage window → flag (likely missed shot, or just a long transition).
- Shot count differs significantly from typical IPSC stage round counts → informational note (we don't know exact round count).

**`compare/`** — Multi-shooter side-by-side FCPXML export. Reads N existing single-shooter `MatchProject` directories (all from the same match) and emits one FCPXML where each stage is a beep-aligned grid compound clip. Each shooter must already have per-stage lossless trims on disk.
- `manifest.py`: `CompareManifest` Pydantic model + `load_manifest` YAML loader. Validates `audio_from` matches a label, label uniqueness, and resolves relative paths against the manifest's parent dir.
- `project_loader.py`: `load_shooter` walks `MatchProject.stages`, derives the per-stage trim path via the same `_slugify` rule the per-stage exporter uses, and computes `beep_offset_in_clip = min(trim_pre_buffer_seconds, primary.beep_time)`. Stages without a primary, beep, trim, or marked `skipped` are omitted.
- `layout.py`: pure math. `choose_grid(roster_count)` picks the smallest of `{1up, 2up-h, 2up-v, 2x2, 3x3, 4x4}` that fits the full roster (sized for the manifest, not the present subset, so slot indices stay stable across stages). `compute_layout(...)` returns a `GridLayout` with `slots_per_label` (alphabetical) and `empty_slots` for filler placement.
- `filler.py`: `ensure_filler` shells out to ffmpeg (`-f lavfi -i color=c=black -an`) to produce a silent black mp4 sized to the longest tile in the stage. Filename encodes `(W, H, fps_num, fps_den, duration_ms)` so stages with matching geometry share one file.
- `emitter.py`: builds the FCPXML. Sequence format from the audio-source shooter; per-tile assets dedup formats via the same `formats_by_key` pattern as `fcpxml_gen.generate_match_fcpxml`. Slot 0 (alphabetically first present label) is the spine clip; others on lanes 1..N-1; filler tiles take later lanes. Beep alignment: `delta = round((max_beep - tile_beep) / fd)` so every tile's clip-local beep coincides at the same parent timeline frame. Audio: `<adjust-volume amount="-96dB"/>` on every tile except `manifest.audio_from`. Outer spine: one `<ref-clip>` per stage with a `<marker>` named `Stage N -- <name>`.
- `cli.py`: Typer sub-app exposing `splitsmith compare export <manifest>`.

## Data structures

```python
# Pydantic models in config.py

class StageData(BaseModel):
    stage_number: int
    stage_name: str
    time_seconds: float
    scorecard_updated_at: datetime

class Shot(BaseModel):
    shot_number: int          # 1-indexed
    time_absolute: float      # seconds from audio start
    time_from_beep: float     # seconds from beep
    split: float              # seconds since previous shot (or from beep for shot 1)
    peak_amplitude: float
    confidence: float         # 0.0 to 1.0
    notes: str = ""

class StageAnalysis(BaseModel):
    stage: StageData
    video_path: Path
    beep_time: float
    shots: list[Shot]
    anomalies: list[str]

class RawVideo(BaseModel):
    """One uploaded raw video file attached to a match.
    
    A raw video is the original camera recording the user uploaded once;
    a single 30-minute head-cam clip typically covers multiple stages.
    StageVideo entries are the per-stage references that point at this
    raw via storage_path - N:1 relationship (many StageVideos can
    resolve to the same RawVideo when one source covers multiple stages).
    """
    original_filename: str
    size_bytes: int = 0
    sha256: str | None = None              # populated on hosted uploads
    uploaded_at: datetime
    storage_path: str                      # project-relative or absolute path
    covers_stages: list[int]               # stage numbers this take spans
    duration_seconds: float | None = None  # backfilled by detect-beep worker
    recorded_start: datetime | None = None # wall-clock UTC, backfilled from
                                           # st_birthtime or mtime - duration

class StageVideo(BaseModel):
    """One video file assigned to a stage.
    
    Assigned videos hash "<path>#<stage_number>" so one source file
    covering N stages yields N distinct video_ids (per-video API routes
    and cache filenames stay collision-free).
    """
    path: Path
    role: Literal["primary", "secondary", "ignored"]
    beep_time: float | None = None
    beep_window: tuple[float, float] | None = None  # (start_s, end_s)
                                                    # into source file
    beep_window_source: Literal["scoreboard",
                                "sequential",
                                "manual"] | None = None
    stage_number: int | None = None        # stamped by MatchProject,
                                           # feeds video_id computation
    # ... other fields
```

## Configuration

Defaults live in code; users can override via `config.yaml` in the working directory.

```yaml
beep_detect:
  freq_min_hz: 2000
  freq_max_hz: 5000
  min_duration_ms: 150
  min_amplitude: 0.3

shot_detect:
  min_gap_ms: 80
  onset_delta: 0.07          # librosa onset_detect delta param
  pre_max_ms: 30
  post_max_ms: 30

video_match:
  tolerance_minutes: 15
  prefer_ctime: true

output:
  trim_buffer_seconds: 5.0
  fcpxml_version: "1.10"
  split_color_thresholds:
    green_max: 0.25
    yellow_max: 0.35
    transition_min: 1.0      # splits above this aren't colored as splits
```

## Testing

Every detection module gets fixture-based tests. Fixtures live in `tests/fixtures/` as short audio/video clips with hand-labeled ground truth in adjacent JSON files.

```
tests/fixtures/
├── beep_only.wav
├── beep_only.json          # {"beep_time": 1.234}
├── stage_short.mp4
├── stage_short.json        # {"beep_time": ..., "shots": [...]}
└── ...
```

Tests assert detection within tolerance (e.g., shot timestamps within ±15ms of ground truth). Run with `uv run pytest`.

## CLI design

Using Typer:

```bash
# Batch process
splitsmith process \
    --videos PATH \
    --stages PATH \
    --output PATH \
    [--config PATH] \
    [--no-trim] \
    [--no-fcpxml] \
    [--no-csv]

# Single stage
splitsmith single \
    --video PATH \
    --time SECONDS \
    --output PATH \
    [--stage-name TEXT] \
    [--config PATH]

# Detect-only (just print results, no files)
splitsmith detect \
    --video PATH \
    --time SECONDS

# Regenerate FCPXML from a CSV (after manual edits)
splitsmith fcpxml \
    --csv PATH \
    --video PATH \
    --output PATH

# Multi-shooter comparison: render N shooters' beep-aligned trims as
# a per-stage grid into one FCPXML.
splitsmith compare export PATH/TO/manifest.yaml
```

The `fcpxml` regeneration command matters — the user will manually fix detection errors in the CSV and want to rebuild the timeline.

The `compare` command reads N existing single-shooter projects (each with per-stage lossless trims already exported) plus a manifest YAML naming them, and emits one FCPXML where each stage is a beep-aligned grid compound clip. See `compare/` under module responsibilities for the per-module breakdown and `examples/compare-bromma-classifier-2026.yaml` for an annotated manifest.

## Error handling principles

- Fail loudly on missing inputs, unsupported video formats, missing ffmpeg.
- Soft-fail per stage during batch: if stage 3 errors, log it and continue to stage 4.
- Always write a report file even on partial success — the report is the audit trail.
- Use `rich` for output: progress bars, colored warnings, formatted tables.

## What NOT to add (yet)

- ML-based shot classification.
- Direct upload to YouTube / cloud storage.
- Web UI.
- Real-time analysis modes.
- Auto-suggesting training drills based on splits.

These might be valuable later but are explicitly out of scope for v1. Ship the boring useful core first.

## Production UI v1 (issue #11/#12) — match-project on-disk layout

The production UI (`splitsmith ui --project PATH`) treats a *match* as the persistence unit. A match project is a directory on disk with a fixed layout; the UI is just a view over it. Videos can be added to an existing match at any time (head-cam now, bay-cam from a friend a day later) without invalidating prior audit work — the model is intentionally append-friendly.

### Directory layout

```
<project-root>/
  project.json              # MatchProject metadata + per-stage index
  raw/                      # original video files (or symlinks)
  audio/                    # extracted .wav cache
                            # per-stage/per-video: stage<N>_cam_<video_id>*.wav
                            # per-stage peaks: stage<N>_cam_<video_id>*.peaks-*.json
                            # take-wide peaks (key = blake2s of storage_path): take_<key>.peaks-*.json
                            # take audio (8 kHz mono): take_<key>.wav
  trimmed/                  # per-stage trimmed MP4s (Sub 5 / #16)
  audit/                    # per-stage audit JSON (same shape as fixture format)
  exports/                  # CSV / FCPXML / report.txt
  scoreboard/               # cached SSI JSON + raw fetch responses
```

### `project.json` shape

Pydantic model `splitsmith.ui.project.MatchProject`. All writes go through `atomic_write_json` (temp file + `os.replace`) so a crashed save can never corrupt the file.

```jsonc
{
  "schema_version": 1,
  "name": "Tallmilan 2026",
  "created_at": "2026-04-30T08:10:32+00:00",
  "updated_at": "2026-05-01T19:28:21+00:00",
  "competitor_name": "Mathias",
  "scoreboard_match_id": "ssi-12345",
  "stages": [
    {
      "stage_number": 3,
      "stage_name": "Per told me to do it",
      "time_seconds": 14.74,
      "scorecard_updated_at": "2026-04-26T16:41:00+00:00",
      "skipped": false,
      "videos": [
        {
          "path": "raw/VID_20260426_162417.mp4",
          "role": "primary",
          "added_at": "2026-04-26T18:00:00+00:00",
          "processed": { "beep": true, "shot_detect": true, "trim": true },
          "beep_time": 12.453,
          "beep_window": [0.0, 60.0],
          "beep_window_source": "scoreboard",
          "notes": ""
        },
        {
          "path": "raw/baycam_friend.mp4",
          "role": "secondary",
          "added_at": "2026-04-28T09:14:00+00:00",
          "processed": { "beep": true, "shot_detect": false, "trim": true },
          "beep_time": 12.501,
          "beep_window": null,
          "beep_window_source": null,
          "notes": "bay cam from friend, added 2 days post-match"
        }
      ]
    }
  ]
}
```

### Per-video role and pipeline

| `role`    | beep detect | shot detect | trim |
|-----------|:-----------:|:-----------:|:----:|
| primary   |     ✓       |     ✓       |  ✓   |
| secondary |     ✓       |     —       |  ✓   |
| ignored   |     —       |     —       |  —   |

The **primary** is the audit truth — detection runs on its audio and audit JSON timestamps live on its timeline. **Secondaries** are alternate viewing angles; their beep is detected so they can be aligned to the primary by beep offset, but their shots are not detected (the primary's audit is the truth). **Ignored** videos are skipped entirely (warmups, neighbor-bay grabs).

The pipeline is **idempotent**: re-entering ingest only processes videos with `processed.* == false`. Adding a secondary to an audited stage runs only beep + trim for that file; the audit JSON is untouched.

### Accessibility (locked, see #21)

- **WCAG 2.2 Level AA** is the target across the production UI.
- **Color is never the only signal** (WCAG 1.4.1). Splits, statuses and audit markers all carry shape, glyph, or text in addition to color.
- **Color-blind safe palette** (Okabe-Ito derived) for split colors. Distinguishable under deuteranopia, protanopia, and tritanopia.
- `fcpxml_gen.py` emits band names as text (`[GREEN]` / `[YELLOW]` / `[RED]` / `[BLUE]`); FCP renders marker color from FCP-side preferences. The in-app palette and FCPXML output are decoupled.
- The `MarkerGlyph` component encodes audit-marker state by shape (filled triangle / outline triangle with strikethrough / dashed diamond) so users who can't perceive color can still distinguish detected / rejected / manual.
- `prefers-reduced-motion` is respected globally.

### Implementation status

Sub 1 (#12) shipped: `MatchProject` Pydantic model + atomic write, FastAPI backend, React + Vite + shadcn/ui SPA, locked design tokens with `/_design` visual spec, app shell, dark/light/system theme toggle.

Sub 2 (#13) shipped: ingest screen + supporting backend.

- `MatchProject.unassigned_videos` plus helper methods: `import_scoreboard`, `register_video` (symlinks into `<project>/raw/`, falls back to copy on systems without symlink support), `assign_video` (move between stages or back to unassigned, with primary-demotion semantics), `auto_match` (runs `video_match.py` heuristic, returns suggestions without mutation).
- API: `POST /api/scoreboard/import`, `POST /api/videos/scan`, `POST /api/videos/auto-match`, `POST /api/assignments/move`. Scoreboard import refuses overwriting existing stages by default (would orphan video assignments) — caller passes `overwrite=true` to force.
- Ingest screen: drop SSI JSON, scan a folder of videos by path, see auto-suggested primary assignments, drag/click to reassign, mark as ignored, unassign back to tray, conflict highlighting on duplicate primaries.

Subsequent sub-issues fill in the audit screen (#15), short-GOP trim (#16), and analysis/export (#17).

## References

- librosa onset detection: https://librosa.org/doc/main/generated/librosa.onset.onset_detect.html
- FCPXML format reference: https://developer.apple.com/documentation/professional_video_applications/fcpxml_reference
- ffmpeg lossless cutting: https://trac.ffmpeg.org/wiki/Seeking
