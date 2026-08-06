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
- A mix of every shooter as track 1 (the default track), then one named track
  per shooter as tracks 2..N+1
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

   **`--overlay` asks for two more things**, and this is where a host that
   renders the plain grid perfectly can still come up short. A version number
   does not answer either question -- both depend on how the binary was
   configured -- so the renderer probes the resolved binary before it encodes
   anything. You can run the same checks by hand:

   ```bash
   ffmpeg -hide_banner -h filter=drawtext | head -2   # the running clock
   printf "file '/nonexistent.png'\noption framerate 30/1\n" > /tmp/p.txt
   ffmpeg -hide_banner -f concat -safe 0 -i /tmp/p.txt -f null -   # the sprite timing
   ```

   - `Unknown filter 'drawtext'` means the build has no `--enable-libfreetype`.
     Common on distro and static builds. **The render still runs**; see
     "Degraded output" below.
   - `Line 2: unknown keyword 'option'` (exit 183) means the concat demuxer is
     too old for the overlay's sprite input. `--overlay` is **refused up front**
     with that reason; the plain grid is unaffected and still renders. The
     second command failing on the missing file instead is the healthy answer.

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

`--audio-from` no longer chooses which track plays. Track 1 is always a mix of
every shooter and always the default, so anything that reads track 1 only --
YouTube, browser `<video>`, every social player -- hears the whole squad. What
`--audio-from` still does on this path is set the render's frame rate, which is
taken from that shooter's lowest-numbered stage. (On the FCPXML path it is also
the one unmuted tile.)

Optional: `--camera "<shooter>=chest"` (repeatable) to pick a specific camera per
shooter, by mount or by role.

Progress prints per stage. If the terminal goes quiet for many minutes with no
stage line, that is a hang, not slowness.

## The end-of-stage summary hold (`--summary-hold`)

```bash
uv run splitsmith compare export <MATCH_PATH> \
    --format mp4 --audio-from "<shooter>" \
    --overlay --summary-hold 3 \
    -o ~/grid.mp4
```

At the end of every stage each tile freezes on its last frame, blurred and
dimmed, and that shooter's stage summary draws over their own cell -- shot
count, stage time, hit factor, `stage_pct`, hit counts and their placing
against the other shooters. The live overlay stops dead at the freeze: no
counters, no split labels, no running clock over the summary.

Three things to know before running it on a real match:

- **It requires `--overlay`** and is refused without it, by name. The summary
  is the overlay's own data in the overlay's own typography.
- **It is charged per stage.** `--summary-hold 3` on a 12-stage match adds 36
  seconds of video and a comparable slice of encode time. Values over 30s
  warn (and still render) because they are almost always a typo.
- **Off by default.** With no `--summary-hold`, the ffmpeg command line is
  byte-identical to the pre-Milestone-B one.

**This changes the durations below**, which is why they are stated as formulas
rather than numbers.

## Verifying the result -- this is the actual deliverable

## Degraded output: an ffmpeg with no `drawtext`

Only the overlay's **running clock** is `drawtext`. The per-tile shot counters
and last splits are pre-rendered PNGs composited with `overlay`, which every
ffmpeg has. So a build without `--enable-libfreetype`
loses one number per tile rather than the whole feature, and the render says so
twice -- once before it starts encoding, and once on the last line:

```
Note: /usr/bin/ffmpeg (ffmpeg 6.1.1) has no usable drawtext filter, so the
overlay's running clock is omitted. The per-tile shot counters and last splits
still render. For the clock, use an ffmpeg built with
--enable-libfreetype, and point both SPLITSMITH_FFMPEG and SPLITSMITH_FFPROBE
at it -- a mismatched pair is its own source of confusing failures.
...
Wrote ~/grid.mp4 (12/12 stages, running clock omitted: this ffmpeg was built
without drawtext)
```

What that file looks like: every tile's top-**left** corner still carries its
shot counter and the bottom-center of each cell still carries the last split --
and each tile's top-**right** corner, where the elapsed time would tick, is
empty. Nothing else changes: same canvas, same frame
rate, same N+1 audio tracks, same duration. A file whose counters are *also*
missing is a different bug, not this degradation.

If you want the clock, the fix is an ffmpeg built with `--enable-libfreetype`
(most static builds from johnvansickle / BtbN have it), with **both** env vars
pointed at the same install:

```bash
SPLITSMITH_FFMPEG=/opt/ffmpeg/bin/ffmpeg \
SPLITSMITH_FFPROBE=/opt/ffmpeg/bin/ffprobe \
uv run splitsmith compare export ...
```

If the run **refuses** `--overlay` outright with a message about the concat
demuxer's `option` keyword, that is the other check from the prerequisites, and
the same fix applies. Re-running without `--overlay` gets the plain grid on that
host in the meantime.

### macOS: Homebrew's default `ffmpeg` has no `drawtext`

Hit for real by a user running this exact export on macOS: every stage failed.
Root cause is the formula, not the host. Homebrew's default `ffmpeg` (the 8.x
formula as of this writing) is a slimmed build with no freetype/harfbuzz, so
`drawtext` does not exist -- not "misconfigured", not present-but-broken, the
filter is compiled out. `brew install ffmpeg` alone will not fix this; it is
the formula that lacks the feature, not the version.

The fix is the sibling formula that keeps the full feature set:

```bash
brew install ffmpeg-full
```

`ffmpeg-full` is **keg-only** -- Homebrew will not link it over the default
`ffmpeg`, on purpose, because the two formulae conflict. That means installing
it changes nothing about what `ffmpeg` on `PATH` resolves to; you have to point
splitsmith at it explicitly, and **both** variables, because they are a matched
pair from the same keg:

```bash
export SPLITSMITH_FFMPEG="$(brew --prefix ffmpeg-full)/bin/ffmpeg"
export SPLITSMITH_FFPROBE="$(brew --prefix ffmpeg-full)/bin/ffprobe"
```

Confirm before re-running the export:

```bash
"$SPLITSMITH_FFMPEG" -hide_banner -h filter=drawtext | head -2   # must not say "Unknown filter"
```

"Use a build with `--enable-libfreetype`" is not, by itself, actionable advice
on macOS -- there is no `./configure` step in a Homebrew install. The formula
name is what closes the loop.

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

Expect: `3840,2160,<the footage's rate>`, then **N+1** audio lines for an
N-shooter match:

```
0,Mix,1
1,<alphabetically first shooter>,0
...
N,<alphabetically last shooter>,0
```

The mix must be index 0 and must be the only one with `default=1`; the shooters
must follow in alphabetical order, each with its own `handler_name`. A mix in
any later slot is a mix nobody outside an NLE will hear. `SoundHandler` instead
of a name means the stitch stopped restating the metadata -- stream copy does
not carry it across the concat demuxer. A single audio track is a regression of
a defect already fixed once.

Then check the mix is a mix of *everyone*, which the track list cannot show:

```bash
ffmpeg -hide_banner -i ~/grid.mp4 -map 0:a:0 -af volumedetect -f null - 2>&1 | grep volume
ffmpeg -hide_banner -i ~/grid.mp4 -map 0:a:1 -af volumedetect -f null - 2>&1 | grep volume
```

The mix's `mean_volume` should sit roughly 10*log10(N)/2 dB under a single
shooter's track -- about -6 dB at N=4 -- and its `max_volume` must stay below
0 dB. A mix *louder* than a single shooter means the `normalize=1` on `amix` was
lost and the track is clipping.

### 1b. With `--summary-hold`, check the length -- from decoded frames

**Do not use `ffprobe`'s `duration` field for this, and do not use its
per-packet durations either.** On these files the mov muxer stretches the last
coded frame of a segment rather than adding frames, and `ffprobe` reports a
uniform packet duration straight across the join. Both lie about exactly the
thing you are checking. Count what decodes:

```bash
# Video: frames actually decoded.
ffmpeg -hide_banner -i ~/grid.mp4 -map 0:v:0 -f null - 2>&1 | tail -1
# Audio: decoded AAC frames x 1024 / sample_rate, per track.
for s in 0 1 2 3 4; do
  ffprobe -v error -select_streams "a:$s" -count_frames \
    -show_entries stream=nb_read_frames,sample_rate \
    -of default=noprint_wrappers=1 ~/grid.mp4
done
```

Expect, for S stages rendered with `--summary-hold H`:

- **video frames** = `sum(action frames per stage) + S x H x fps`
- **audio seconds** = video seconds, within one video frame plus one AAC frame
  (~54ms at 30fps/48kHz), and **identical across every track**

Two failures to look for, neither of which announces itself:

- **Audio longer than video by exactly `S x H`.** That is the summary hold
  with no summary in it -- the still never reached the filter graph. It exits
  0, prints no warning and freezes at the right instant on the raw last action
  frame. The only way to see it is to look at a frame inside a hold (below).
- **Audio *shorter* than video**, growing with stage count. That is the
  opposite fault and it drifts: every stage after the short one plays its
  sound early, cumulatively.

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
- **Listen to track 1 without switching anything.** This is what a viewer on
  YouTube or a phone gets. Every shooter's shots should be audible in it; one
  shooter's mic dominating, or a shooter missing entirely, is the defect this
  track exists to prevent.
- **Then switch audio tracks.** In QuickTime, VLC or Final Cut. Track 1 is
  "Mix"; each of the rest should be the corresponding shooter's audio alone.

If you rendered with `--summary-hold`, also check the end of a stage. This is
the one part of the feature a passing test suite on synthetic footage cannot
settle, because it is a judgement about whether the handoff reads as a
conclusion or as a stall:

- **Is the held picture blurred and dimmed?** A hold showing a *sharp* last
  frame means the summary still never reached the graph -- the "audio longer
  than video" fault above. Everything else about that file looks correct.
- **Is the live overlay gone?** No shot counters, no split labels, and above
  all no clock. A frozen clock beside a summary is the failure the freeze
  exists to prevent.
- **Are the figures each shooter's own, in their own cell?** They should sit
  where you have been watching that shooter, not in a strip.
- **Do the figures change from stage to stage?** Identical summaries across
  two different stages means every segment got the same still.
- **Is `--summary-hold 3` long enough to read on real footage?** Four lines is
  comfortable at 3s on synthetic media at 640x360; a full scorecard is closer
  to seven lines at 4K. If it is not, that is a value to change, not a bug.
- **Does the next stage start cleanly?** The cut back to live footage is hard
  -- there is no fade in either direction, by design, since transitions force
  a re-encode at the concat seams and are deferred.

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
  3840x2160, audio track count still 3 shooters plus the mix (empty cells add
  video only -- an extra audio track there would break the concat stitch, and
  would drag a silent input into the mix and cost the real tiles level).
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
The usual cause is trims that are not actually on disk. A feature-poor ffmpeg
used to land here too -- every stage failing the same way, an hour in, with
nothing to show for it. That is what the pre-flight above exists to catch, so if
you see it, capture the stderr: it means a capability nothing probes for.

Capture and bring back:

```bash
uv run splitsmith compare export ... 2>&1 | tee ~/grid-render.log
ffprobe -v error -show_format -show_streams ~/grid.mp4 > ~/grid-probe.txt 2>&1
```

Plus a note on what the video *looked* wrong about, if anything. That last part
is the one thing no log captures and the only thing that closes Task 8.

## What is deliberately not in this branch

Phase 0, plus the `--overlay` pre-flight documented above (added later, after
this runbook's host hit exactly the failure it prevents), plus Milestone B's
`--summary-hold`. No transitions, no title cards, no end-of-match summary, no
hosted-mode support, and no FCPXML from the new UI surface.
The FCPXML grid
(`splitsmith compare export <match> -o out.fcpxml`, the default `--format`) is
untouched and behaves exactly as before.

Design and full plan: `docs/superpowers/specs/2026-08-04-compare-grid-mp4-and-export-redesign-design.md`
and `docs/superpowers/plans/2026-08-04-compare-grid-mp4-phase-0.md`.
