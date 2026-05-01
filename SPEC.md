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
[2] Detect start beep              (beep_detect.py)
    │   bandpass filter, peak detection
    ▼
[3] Trim video losslessly          (trim.py)
    │   ffmpeg -c copy from beep-5s to beep+stage_time+5s
    ▼
[4] Detect shots                   (shot_detect.py)
    │   librosa onset detection within stage time window
    ▼
[5] Generate outputs               (csv_gen.py, fcpxml_gen.py, report.py)
    │
    ▼
CSV + FCPXML + report per stage
```

### Module responsibilities

**`cli.py`** — Typer-based CLI. Two main commands: `process` (batch, takes stage JSON) and `single` (one video, explicit time). Handles output directory creation and orchestration. No analysis logic here.

**`video_match.py`** — Match raw video files to stages. Strategy:
1. Read video file creation/modification time (prefer ctime where available).
2. Compare against `scorecard_updated_at` from stage JSON, with configurable tolerance (default ±15 minutes).
3. If multiple videos match a stage or none match, prompt for manual mapping.
4. Output: `dict[stage_number, video_path]`.

Be aware: `scorecard_updated_at` is when the score was *typed in*, not when the stage was shot. Real shoot time is typically 1–10 minutes earlier. Bias the matching window accordingly.

**`beep_detect.py`** — Find the start beep timestamp in the audio.
- Most shot timer beeps are pure tones in the 2.5–4 kHz range, lasting 200–400ms.
- Approach: bandpass filter audio to 2000–5000 Hz, compute envelope, look for sustained energy with sharp onset.
- Return: timestamp in seconds (float) of the beep's leading edge.
- Must be robust to: ambient match noise, RO commands, distant beeps from other bays.
- Heuristic for false-positive rejection: the beep is followed within `stage_time + 1s` by gunshot transients. If candidate beep is not, skip it.

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

**`trim.py`** — Lossless video trim via ffmpeg subprocess.
- Command: `ffmpeg -ss <start> -i <input> -to <duration> -c copy <output>`
- Note: `-ss` before `-i` is fast but may not be frame-accurate. For our purposes (we want a buffer before the beep anyway) this is fine.
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
```

The `fcpxml` regeneration command matters — the user will manually fix detection errors in the CSV and want to rebuild the timeline.

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
          "notes": ""
        },
        {
          "path": "raw/baycam_friend.mp4",
          "role": "secondary",
          "added_at": "2026-04-28T09:14:00+00:00",
          "processed": { "beep": true, "shot_detect": false, "trim": true },
          "beep_time": 12.501,
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
