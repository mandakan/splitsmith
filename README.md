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

- macOS (primary target -- should work on Linux but FCP integration is Mac-only)
- Python 3.11+
- `uv` for dependency management (`brew install uv`)
- `ffmpeg` and `ffprobe` (`brew install ffmpeg`)

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
    --output /tmp/demo_analysis \
    --stage-name "Per told me to do it" \
    --stage-number 3

# Inspect what got produced
ls -la /tmp/demo_analysis/
cat /tmp/demo_analysis/stage3_per-told-me-to-do-it_report.txt
open /tmp/demo_analysis/stage3_per-told-me-to-do-it.fcpxml   # opens in Final Cut Pro
```

Expected output:
- beep detected at ~19.87s in the source video
- ~39 shot candidates detected (the audited ground truth is 14; the rest are echoes / neighbouring bays)
- Anomaly report flags the high shot count and the 928 ms overshoot vs official stage time

To get a clean 14-shot timeline from the demo, see the cull workflow under [`fcpxml`](#fcpxml----regenerate-timeline-from-a-possibly-hand-edited-csv) above.

## Output file layout

```
analysis/
  stage3_per-told-me-to-do-it_trimmed.mp4   # lossless cut around the beep
  stage3_per-told-me-to-do-it_splits.csv    # editable; the source of truth for fcpxml regen
  stage3_per-told-me-to-do-it.fcpxml        # open in Final Cut Pro; M / shift+M to navigate markers
  stage3_per-told-me-to-do-it_report.txt    # human-readable summary + anomalies
```

A `.wav` cache file is written next to each source video on first run -- this is intentional (re-running `detect` on the same video skips the audio extraction). Add `*.wav` to your match-videos directory's `.gitignore` if it's tracked.

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
  fcpxml_version: "1.10"
  split_color_thresholds:
    green_max: 0.25
    yellow_max: 0.35
    transition_min: 1.0
```

Lower `shot_detect.onset_delta` if you're under-detecting shots from a heavily-comped open gun. Tighten `beep_detect.min_amplitude` if a louder ambient noise is being mistaken for the beep.

## Troubleshooting

- **"ffmpeg binary not found" / "ffprobe binary not found"** -- `brew install ffmpeg` (or set `--ffmpeg-binary` if installed elsewhere).
- **`process` aborts with "Ambiguous stages"** -- two videos fall in the same stage's window, or one video matches multiple stages. Either narrow the input directory or use `single` for each stage.
- **Report shows ">> 32 shots, possible false positives"** -- expected on busy ranges. Cull in the CSV and regenerate the FCPXML.
- **Last shot is detected after the official stage time** -- usually a neighbouring-bay shot fired during your last shot's echo window. Drop it in the CSV.
- **No FCPXML markers visible in FCP** -- ensure FCP 11.x or later (FCPXML 1.10 is required). Older versions need an export of the timeline into 1.9.

## Project status

Early development. Built for personal use by an IPSC competitor. PRs welcome but the design priorities reflect that use case.
