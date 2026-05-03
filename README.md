# splitsmith

Extract per-shot split times from head-mounted camera footage of IPSC matches and generate Final Cut Pro timelines with per-shot markers.

## Why this exists

Shot timers like the CED7000 give you splits during practice, but at matches you can't carry one. Head-mounted cameras (Insta360 Go 3S in this case) capture audio that contains all the shot information -- this tool extracts it and turns it into actionable training data.

Inputs:
- Raw video files from a head-mounted camera (MP4)
- Stage time data from match scoring system (JSON, format from SSI Scoreboard)

Outputs (per stage):
- Trimmed video file (lossless cut around the start beep)
- CSV of shot timestamps and splits
- FCPXML file ready to open in Final Cut Pro, with frame-aligned markers per shot
- Human-readable report flagging anomalies (likely missed/false-positive shots)

## Requirements

- macOS (primary target), Linux, or Windows. FCPXML is generated on every platform but Final Cut Pro itself is macOS-only -- on Linux/Windows you'll need to copy the `.fcpxml` to a Mac to open it (or just use the splits CSV directly).
- Python 3.11+
- `uv` for dependency management
  - macOS: `brew install uv`
  - Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `winget install --id=astral-sh.uv -e` (or `irm https://astral.sh/uv/install.ps1 | iex`)
- `ffmpeg` and `ffprobe` on `PATH`
  - macOS: `brew install ffmpeg`
  - Linux: `apt install ffmpeg` (or your distro equivalent)
  - Windows: `winget install --id=Gyan.FFmpeg -e` (or scoop/chocolatey)

## Install

```bash
git clone https://github.com/mandakan/splitsmith.git
cd splitsmith
uv sync
```

Verify install:

```bash
uv run splitsmith --help
uv run pytest -q                # 67 unit tests
uv run pytest -q -m integration # 2 ffmpeg/ffprobe-backed tests against the bundled sample
```

## Subcommands

All commands are run via `uv run splitsmith <subcommand>`. Run `--help` on any of them for the full option list.

### `detect` -- preview only, writes nothing

Use this first when tuning thresholds or sanity-checking a video. It runs beep + shot detection and prints a table; no files are written.

```bash
uv run splitsmith detect \
    --video tests/fixtures/stage_sample.mp4 \
    --time 14.74
```

### `single` -- full pipeline for one video

Process a single video with an explicit stage time. Writes the trimmed mp4, splits CSV, FCPXML, and report into `--output`.

```bash
uv run splitsmith single \
    --video tests/fixtures/stage_sample.mp4 \
    --time 14.74 \
    --output ./analysis \
    --stage-name "Per told me to do it" \
    --stage-number 3
```

Skip individual outputs with `--no-trim`, `--no-csv`, `--no-fcpxml`.

### `process` -- batch over a stage JSON

Match a directory of videos to the stages in an SSI Scoreboard export by file timestamp, then run the `single` pipeline for each matched (stage, video) pair.

```bash
uv run splitsmith process \
    --videos ~/match_raw/ \
    --stages examples/tallmilan-2026.json \
    --output ./analysis
```

If videos can't be matched cleanly (multiple candidates for one stage, no candidate, ambiguity across stages), the offending stages and videos are listed and the run aborts. Re-run with the videos renamed/separated, or use `single` for each stage explicitly.

### `ui` -- production UI (issue #11/#12, in progress)

The localhost SPA that orchestrates the full ingest -> audit -> export workflow. State lives on disk under `--project`; closing the browser and re-running resumes where you left off.

```bash
uv run splitsmith ui --project ~/matches/tallmilan-2026
```

Opens `http://127.0.0.1:5174/` in your default browser. Sub 1 (#12) ships the foundation: app shell, project model, three-screen navigation, design system at `/_design`. The actual ingest / audit / export screens land in #13, #15, #17.

For frontend development:

```bash
cd src/splitsmith/ui_static
pnpm install
pnpm build           # produces dist/ which the FastAPI backend serves
pnpm dev             # Vite dev server on :5173, proxies /api to backend on :5174
```

### `review` -- audit a fixture in a local web UI

Open a single-page browser UI for reviewing detected shots against the fixture's audio (and optional video). Marker pins on the waveform: click to toggle keep/reject, drag to fine-tune time, double-click empty waveform space to add a manual marker. Save (`Cmd+S`) writes back to the fixture JSON's `shots[]`.

```bash
uv run splitsmith review \
    --fixture tests/fixtures/stage-shots-tallmilan-stage2.json \
    --video   VID_20260426_162417_00_048.mp4
```

`--video` is optional. The video offset defaults to the fixture window's lower bound (so passing the original source video Just Works); override with `--video-offset-seconds` if you've pre-trimmed.

Keyboard:

| key | action |
|---|---|
| `Space` | play / pause |
| `M` / `Shift+M` | next / previous marker |
| `Cmd+1` / `Cmd+2` / `Cmd+3` | zoom in / fit / zoom out |
| `Cmd+Z` | undo (toggle / drag / add / delete) |
| `Cmd+S` | save |
| right-click marker | delete (manual) or reject (detected) |

Each save writes the previous fixture to `<fixture>.json.bak` first.

### `audit-apply` -- merge a marked-up candidates CSV into a fixture JSON

When you create a new test fixture from a video, the helper script writes a candidates CSV with an `audit_keep` column at the front. Mark the rows you want to keep (`Y`, `1`, `x`, `yes`, `keep`, or `true` -- case-insensitive), save, then merge into the fixture JSON:

```bash
uv run splitsmith audit-apply \
    --candidates tests/fixtures/stage-shots-blacksmith-h5-candidates.csv \
    --fixture    tests/fixtures/stage-shots-blacksmith-h5.json
```

The fixture's `shots[]` array is rewritten in place; the `_candidates_pending_audit` block is preserved so you can re-audit if you change your mind.

### `fcpxml` -- regenerate timeline from a (possibly hand-edited) CSV

After `single` or `process`, the splits CSV typically contains false positives (echoes, neighbouring bays). The recommended workflow is:

1. Open the splits CSV in any editor (Numbers, Excel, vim).
2. **Sort by `confidence` ascending or `peak_amplitude` ascending** -- false positives cluster at the low end.
3. Delete rows that aren't real shots.
4. Regenerate the FCPXML from the cleaned CSV:

```bash
uv run splitsmith fcpxml \
    --csv analysis/stage3_per-told-me-to-do-it_splits.csv \
    --video analysis/stage3_per-told-me-to-do-it_trimmed.mp4 \
    --output analysis/stage3_per-told-me-to-do-it.fcpxml
```

The `--beep-offset` flag (default 5.0s) tells the regenerator where the beep is in the trimmed video. It must equal `output.trim_buffer_seconds` from the config used during the original trim (5s by default).

## End-to-end demo on the bundled sample

The repo ships with a real Stage 3 sample (Tallmilan 2026, "Per told me to do it!", 14.74s, 14 audited shots) at `tests/fixtures/stage_sample.mp4`. Anyone with the repo can do a full end-to-end run:

```bash
uv run splitsmith single \
    --video tests/fixtures/stage_sample.mp4 \
    --time 14.74 \
    --output ./demo_analysis \
    --stage-name "Per told me to do it" \
    --stage-number 3

# Inspect what got produced
ls -la ./demo_analysis/
cat ./demo_analysis/stage3_per-told-me-to-do-it_report.txt

# Open the FCPXML in Final Cut Pro:
#   macOS:   open ./demo_analysis/stage3_per-told-me-to-do-it.fcpxml
#   Linux:   xdg-open ./demo_analysis/stage3_per-told-me-to-do-it.fcpxml
#   Windows: start .\demo_analysis\stage3_per-told-me-to-do-it.fcpxml
# (FCP itself is macOS-only; on other platforms copy the .fcpxml to a Mac.)
```

Expected output:
- beep detected at ~19.87s in the source video
- ~39 shot candidates detected (the audited ground truth is 14; the rest are echoes / neighbouring bays)
- Anomaly report flags the high shot count and the 928 ms overshoot vs official stage time

To get a clean 14-shot timeline from the demo, see the cull workflow under [`fcpxml`](#fcpxml----regenerate-timeline-from-a-possibly-hand-edited-csv) above.

## Reproducibility / what's in git

Source MP4/MOV recordings are gitignored (multi-GB; not worth git-LFS for a personal tool). The committed inputs are:

- `tests/fixtures/*.wav` -- pre-trimmed audio slices (~1-2 MB each), the canonical input for every detection / classification / eval script.
- `tests/fixtures/*.json` -- audited shot times (ground truth) plus `_candidates_pending_audit` (raw detector output) and a `source` / `fixture_window_in_source` provenance pair naming the original video and time window.

Anyone with the repo can run the full pipeline (beep / shot detection, PANN + CLAP feature extraction, ensemble eval) against the WAVs without touching the source videos. What you cannot reproduce without the original MP4 is the `audit-prep` step itself -- if you want a different trim window, padding, or beep override, you need the source video. That step is "input data preparation" rather than "pipeline reproduction"; the WAV is the canonical artefact downstream.

## Output file layout

```
analysis/
  stage3_per-told-me-to-do-it_trimmed.mp4   # lossless cut around the beep
  stage3_per-told-me-to-do-it_splits.csv    # editable; the source of truth for fcpxml regen
  stage3_per-told-me-to-do-it.fcpxml        # open in Final Cut Pro; M / shift+M to navigate markers
  stage3_per-told-me-to-do-it_report.txt    # human-readable summary + anomalies
```

A `.wav` cache file is written next to each source video on first run -- this is intentional (re-running `detect` on the same video skips the audio extraction). Add `*.wav` to your match-videos directory's `.gitignore` if it's tracked.

## Detection methodology

Splitsmith treats *consistency across stages and matches* as more important than matching any other tool's exact timestamps. This section documents what the detector does, why, and how its output relates to a hardware shot timer like the CED7000.

### Beep detection (`beep_detect.py`)

1. Bandpass-filter the audio to `[freq_min_hz, freq_max_hz]` (default 2-5 kHz, the typical IPSC timer beep range).
2. Compute the Hilbert envelope, lightly smoothed (10 ms moving average).
3. Find candidate runs where the smoothed envelope exceeds `min_amplitude * peak` (default 30%) for at least `min_duration_ms` (default 150 ms).
4. Pick the strongest candidate run.
5. **Backtrack to the leading edge**: walk backward from the candidate's start through the smoothed envelope until reaching `5 * p95(noise)` (with the noise window taken from before the run). That sample is the reported beep time.

The backtrack matters because timer beeps ramp up over ~400 ms; a fixed amplitude threshold would land deep into the rise. Using a multiple of the recording's own pre-beep noise floor adapts to gain, distance, and ambient noise.

### Shot detection (`shot_detect.py`)

1. **Skip the first 500 ms after the beep.** Beep tones are 200-400 ms and human reaction + draw is never under 500 ms on a head-mounted recording.
2. **`librosa.onset.onset_detect`** with spectral flux (default `delta=0.07`, `pre_max=post_max=30 ms`) finds onset frames at ~10.7 ms resolution.
3. **80 ms minimum-gap filter** (greedy): drop onsets within 80 ms of a previously-kept one. Catches close echoes from steel/walls.
4. **150 ms echo refractory**: drop subsequent onsets within 150 ms of a kept onset whose peak amplitude is below 40% of the previous peak. Catches lower-amplitude intra-bay echoes.
5. **Half-rise leading edge**: this is the per-shot time you see in outputs. For each kept onset, find the absolute peak `|audio|` in a 30 ms window around the librosa frame, then report the first sample whose `|audio|` reaches **half** that peak. This is the "leading edge" definition used everywhere downstream.

#### Why half-rise?

| target | property | why we don't use it |
|---|---|---|
| absolute amplitude threshold (CED7000-style) | simple, fast | sensitive to AGC, distance, gain. A quiet AGC-ducked shot crosses the threshold later in its rise than a loud unducked shot, biasing splits. |
| noise-floor-relative threshold | adapts to recording conditions | depends on a tunable "K times noise" knob; biases earlier on slow-rise transients. |
| **half of the local peak (half-rise)** | uses the burst's own peak as reference, so AGC ducking doesn't bias timing; matches what the eye picks when scrubbing a waveform | (the choice) |

Half-rise is the standard "onset" definition in audio-engineering literature for sharp transients. It is **insensitive** to:
- ambient noise levels (uses peak ratio, not absolute energy)
- camera AGC ducking (a quieter shot still has a peak; half-rise lands at the same fractional point)
- recording gain or distance (peak scales linearly; half scales the same way)

It is **sensitive** to:
- the burst's own profile (sharp transients have a sharp leading edge; gradual transients land later)
- the 30 ms peak-search window (transients longer than 30 ms would have their peak underestimated; not a concern for gunshots)

#### Comparing splitsmith times to a CED7000 / Pact / similar

**Don't expect absolute timestamps to match.** A CED7000 typically uses an absolute amplitude threshold; splitsmith uses half-rise. On the same recording the two definitions can differ by 5-15 ms per shot.

**Splits *do* match across recordings.** Because the half-rise definition is internally consistent, the *difference* between two consecutive shot times is comparable across stages, matches, and recording conditions. Any constant per-shot offset cancels in the subtraction. This is the metric that matters for training.

If you ever need to compare absolute times to another timer, expect a small constant offset (typically splitsmith reports 5-15 ms earlier than amplitude-threshold timers because half-rise lands earlier in the rise than a fixed threshold).

### Confidence ranking

Each shot has a `confidence` score = geometric mean of normalized onset strength and normalized peak amplitude (each normalized to the max within the kept set). Sorting CSV rows by confidence ascending puts the most likely false positives (echoes, neighbouring bays) at the top — fast triage when culling.

Real shots that come right after a long pause are AGC-ducked and rank lower in confidence, so don't blindly delete the bottom-N rows. Eyeball timestamps too.

## Configuration

Defaults live in `src/splitsmith/config.py`. Override via `--config path/to/config.yaml`:

```yaml
beep_detect:
  freq_min_hz: 2000
  freq_max_hz: 5000
  min_duration_ms: 150
  min_amplitude: 0.3

shot_detect:
  min_gap_ms: 80
  onset_delta: 0.07
  pre_max_ms: 30
  post_max_ms: 30

video_match:
  tolerance_minutes: 15
  prefer_ctime: true

output:
  trim_buffer_seconds: 5.0
  trim_mode: lossless          # "lossless" (CLI default) or "audit"
  trim_gop_frames: 15          # audit mode: keyframe every N frames (0.5s @ 30fps)
  trim_audit_crf: 20           # audit mode: x264 CRF (lower = better quality, larger files)
  trim_audit_preset: fast      # audit mode: x264 preset
  fcpxml_version: "1.10"
  split_color_thresholds:
    green_max: 0.25
    yellow_max: 0.35
    transition_min: 1.0
```

`trim_mode` controls how `trim.py` cuts videos:

- `lossless` (default): `ffmpeg -c copy`. Instant; archival quality; inherits the source GOP. Insta360 head-cam typically has keyframes every 1-4 seconds.
- `audit`: re-encodes with a short GOP (default 0.5s) so browser `<video>` scrubbing in the production UI's audit screen (#15) lands within ~1 frame of the pointer. Encoding cost is roughly 1-2x realtime on Apple Silicon. Audio is stream-copied either way so the detector's input is bit-exact across modes.

Override per command via `--trim-mode lossless|audit` on `splitsmith single` and `splitsmith process`.

Lower `shot_detect.onset_delta` if you're under-detecting shots from a heavily-comped open gun. Tighten `beep_detect.min_amplitude` if a louder ambient noise is being mistaken for the beep.

## Troubleshooting

- **"ffmpeg binary not found" / "ffprobe binary not found"** -- install via your platform's package manager (macOS `brew install ffmpeg`, Linux `apt install ffmpeg`, Windows `winget install --id=Gyan.FFmpeg`), or set `--ffmpeg-binary` if installed elsewhere.
- **`process` aborts with "Ambiguous stages"** -- two videos fall in the same stage's window, or one video matches multiple stages. Either narrow the input directory or use `single` for each stage.
- **Report shows ">> 32 shots, possible false positives"** -- expected on busy ranges. Cull in the CSV and regenerate the FCPXML.
- **Last shot is detected after the official stage time** -- usually a neighbouring-bay shot fired during your last shot's echo window. Drop it in the CSV.
- **No FCPXML markers visible in FCP** -- ensure FCP 11.x or later (FCPXML 1.10 is required). Older versions need an export of the timeline into 1.9.

## Project status

Early development. Built for personal use by an IPSC competitor. PRs welcome but the design priorities reflect that use case.
