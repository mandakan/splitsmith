# Compare Grid MP4 -- Task 8 handoff to the host holding the match

**Branch:** `feat/compare-grid-mp4-phase-0`
**Purpose:** Phase 0 is code-complete and verified on synthetic footage. Task 8 is
the one step that could not run on the development host: rendering a real
4-shooter match and *watching the result*. This document is what a fresh session
on the match-holding host needs.

Everything below assumes no memory of the session that built this.

## What this branch does

`splitsmith compare export <match> --format mp4` renders N shooters' beep-aligned
per-stage trims into one grid video:

- 4K canvas (3840x2160); each tile in a 2x2 gets 1920x1080
- Frame rate follows the audio-source shooter's footage
- One audio track per shooter, named, with the chosen shooter as the default track
- A shooter missing a stage's trim becomes a black tile that keeps its cell
- A stage whose render fails is reported and skipped; the rest still stitch

It does not run detection and never reads shot data. It reads finished per-stage
trims only.

## Prerequisites on the target host

1. **ffmpeg** on `PATH` (or `SPLITSMITH_FFMPEG` pointing at it). Verified against
   6.1.1 and 7.0.2. It must have `libx264`, `aac`, and the `xstack`, `tpad`,
   `adelay`, `apad`, `anullsrc`, `aformat` filters. Check:

   ```bash
   ffmpeg -hide_banner -filters | grep -E ' (xstack|tpad|adelay|apad|anullsrc|aformat) '
   ffmpeg -hide_banner -encoders | grep -E 'libx264|aac |pcm_s16le'
   ```

2. **Python 3.11+** and **`uv`** (never `pip` in this repo).
3. **Disk headroom.** The renderer writes per-stage segments to a temp work
   directory beside the output before stitching. Budget roughly the size of the
   finished video again, transiently, plus the segments' uncompressed audio:
   they carry PCM rather than AAC so the stitch has no per-segment encoder
   padding to accumulate, which costs ~1.5 Mbps per shooter -- about 260MB
   across a 12-stage 4-shooter match.
4. **Time.** A full-match 4K re-encode is plausibly tens of minutes of CPU. This
   is a re-encode, not a stream copy -- there is no fast path.

## Getting the branch

```bash
git fetch origin feat/compare-grid-mp4-phase-0
git checkout feat/compare-grid-mp4-phase-0
uv sync
```

Confirm the engine is present and healthy before touching real footage:

```bash
uv run pytest tests/test_compare_mp4_grid_render.py -v -m integration
```

All of these must pass, not skip. A **skip** means ffmpeg was not found -- fix
that first, because a skipped suite here proves nothing about the renderer.

## Preflight on the match

The grid reads per-stage lossless trims. If the match has not been through the
trim step, do that first -- it re-cuts every stage for every shooter and is not
instant on 4K footage:

```bash
uv run splitsmith match trims <MATCH_PATH> --dry-run   # see the plan first
uv run splitsmith match trims <MATCH_PATH>
```

`--dry-run` is pure: it reads project files and classifies without touching
media, so it shows exactly what a real run would do.

## The render

```bash
uv run splitsmith compare export <MATCH_PATH> \
    --format mp4 \
    --audio-from "<shooter slug or display name>" \
    -o ~/grid.mp4
```

`--audio-from` chooses whose audio is the **default** track. Every shooter still
ships as a separate selectable track; the choice decides which one plays first.
It matters for anything published, because YouTube, browser `<video>` and social
players read track 1 only.

Optional: `--camera "<shooter>=chest"` (repeatable) to pick a specific camera per
shooter, by mount or by role.

Progress prints per stage. If the terminal goes quiet for many minutes with no
stage line, that is a hang, not slowness.

## Verifying the result -- this is the actual deliverable

A green test suite is not evidence the video is right. Every genuine defect on
this branch was found by rendering and measuring. Do both of the following.

### 1. Probe the container

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,r_frame_rate -of csv=p=0 ~/grid.mp4
ffprobe -v error -select_streams a \
  -show_entries stream=index:stream_tags=handler_name:stream_disposition=default \
  -of csv=p=0 ~/grid.mp4
```

Expect: `3840,2160,<the footage's rate>`, then one audio line per shooter, named,
with `default=1` on exactly the shooter passed to `--audio-from` -- **not** the
alphabetically-first one. A single audio track, or the default landing on the
wrong shooter, is a regression of a defect already fixed once.

### 2. Watch it

Open the file. Check, in order:

- **Do the shots line up across tiles?** This is the whole point of the feature.
  Pick a stage, watch all four shooters' first shot. They should be
  simultaneous. If one tile consistently runs early, that is the beep-alignment
  path failing.
- **Are the tiles the right shooters, in stable cells across stages?** A shooter
  must occupy the same cell in every stage.
- **Is any cell black that should not be?** Black means no trim for that
  shooter on that stage. Verify that matches reality rather than a lookup bug.
- **Switch audio tracks.** In QuickTime, VLC or Final Cut. Each should be the
  corresponding shooter's audio.

## Known issues -- all fixed, listed so you can spot a regression

These were found by the whole-branch review and fixed in `14f6a95` / `0faac22`.
They are recorded because each is a plausible regression and each is invisible
to a passing test suite:

- **Empty grid cells rendered bright green.** `xstack` defaults to `fill=none`,
  leaving unused regions as raw frame buffer -- YUV(0,0,0), which is
  RGB(0,135,0). It hit rosters of 3, 5, 7, 8 and 10-15; a 4-shooter 2x2 is full
  and was never affected. Now filled with black inputs per
  `layout.GridLayout.empty_slots`, matching what `emitter.py` already did.
  Verified on a 3-shooter render: empty quadrant RGB(0,0,0), canvas still
  3840x2160, audio track count still 3 (empty cells add video only -- an extra
  audio track there would break the concat stitch).
- **A selected stage no shooter had a trim for was silently dropped and reported
  as complete success.** Requesting stages 1-3 with trims for 1 and 2 returned
  "Rendered all 2 stages" with a green tick. Counts are now against the
  *requested* stages, and the missing shooter/stage pairs are reported.
- **The UI progress bar sat at 5% for the entire render.** Now reports per
  stage, matching the CLI.

If any of these reappears, it means a regression rather than a new bug -- the
fixes have covering tests, but the green-cells one in particular can only fail a
test whose roster leaves an empty cell (3 or 5 shooters). A 2- or 4-shooter test
cannot detect it, which is exactly why it shipped past seven reviews.

## If it fails

Per-stage failures are isolated: the run continues and reports which stages
failed with trimmed ffmpeg stderr. That output is the diagnostic -- capture it.

If *every* stage fails, the run raises rather than writing a zero-byte file.
The usual causes are a missing or feature-poor ffmpeg, or trims that are not
actually on disk.

Capture and bring back:

```bash
uv run splitsmith compare export ... 2>&1 | tee ~/grid-render.log
ffprobe -v error -show_format -show_streams ~/grid.mp4 > ~/grid-probe.txt 2>&1
```

Plus a note on what the video *looked* wrong about, if anything. That last part
is the one thing no log captures and the only thing that closes Task 8.

## What is deliberately not in this branch

Phase 0 only. No splits overlay, no transitions, no title cards, no hosted-mode
support, and no FCPXML from the new UI surface. The FCPXML grid
(`splitsmith compare export <match> -o out.fcpxml`, the default `--format`) is
untouched and behaves exactly as before.

Design and full plan: `docs/superpowers/specs/2026-08-04-compare-grid-mp4-and-export-redesign-design.md`
and `docs/superpowers/plans/2026-08-04-compare-grid-mp4-phase-0.md`.
