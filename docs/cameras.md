# Cameras

What each camera's footage does to detection, and how to recognise its files. Read this before
changing a detection threshold, a voter C feature or a camera class, and before deciding a result
is a model problem rather than a camera one. Add to it whenever a measurement says something new
about a camera; date every finding, because firmware changes what a camera records.

Detection sees only the mono mix of a video's first audio stream (`ui/audio.py` extracts with
`-ac 1`). Calibration has two camera classes, `headcam` and `handheld`, chosen by the video's mount
(`ensemble.calibration.camera_class_from_mount`); there is no per-model model, only the per-model
amplitude floor (`amp_floor_by_camera_model`).

## Recognising a file

| Camera | Mount | File name | Metadata | Audio stream |
|---|---|---|---|---|
| Insta360 GO 3S | head | `VID_YYYYMMDD_HHMMSS_00_NNN.mp4` | none identifying (no make, model or encoder tag) | AAC 2 ch, 48 kHz, both channels the same |
| Meta (Oakley) Vanguard | head | `video-NNNN_singular_display.mov` | `com.apple.quicktime.model=Vanguard`, `comment` has `device=Vanguard`, `description` carries a version string | AAC 2 ch, 48 kHz, real stereo |
| DJI Osmo Action 4 | head (chest possible) | `DJI_YYYYMMDDHHMMSS_NNNN_D.MP4` | `encoder=DJI OsmoAction4` | AAC 2 ch |
| iPhone 17 Pro / Pro Max | hand | `IMG_NNNN.MOV` (often renamed) | `com.apple.quicktime.make=Apple`, `model`, `software` (iOS version) | AAC 2 ch real stereo, plus a 4 ch Apple APAC spatial stream ffmpeg cannot decode |
| Samsung phone | hand | `YYYYMMDD_HHMMSS.mp4` | `com.android.version`, `com.samsung.android.utc_offset`; no model tag | AAC 2 ch, variable frame rate |

`ffprobe -v error -show_entries format_tags:stream=codec_name,channels -of compact <file>` shows
all of it. The fixture's `camera` block is often `unknown`; the file name in `source_video` is the
reliable key for older fixtures.

## Insta360 GO 3S (head)

- **Mono.** Left and right correlate at 0.997; stereo features carry nothing (2026-10-09).
- **Heavily compressed.** Shots peak around -4 to -7 dBFS and stand only about 17 dB above the
  sound between them (23 dB for the first shot of a string), against about 42 dB on the Vanguard.
  Walking and wind noise sit close to shot level (2026-10-09, 37 headcam fixtures).
- **Shots lose treble through a string.** Level barely moves, but each successive shot in a string
  (gaps up to 1.0 s) is duller: spectral centroid of the first 40 ms falls from about 980 Hz on
  shot 1 to about 700 Hz from shot 6 on, and energy above 4 kHz drops 5.6 dB. Audible as shots
  getting "more and more muffled". Held-out voter C recall falls with it, 0.985 on shot 1 to 0.64
  from shot 6 on; late-string misses are most of the GO 3S misses (2026-10-09). Seen in footage
  from April and September 2026, so not a recent firmware change.
- Frame rate 29.97 or 50 fps depending on the setting, 16:9.

- **Stage-relative voter C (2026-10-09).** Voter C now also judges each candidate against the
  stage's likely shots (spec `2026-10-09-voter-c-stage-relative-features-design`). Accepted trade:
  held out by match, Blacksmith 2026 GO 3S precision fell from 0.944 to 0.825 (recall 0.905 to
  0.926) in exchange for surviving camera audio changes. Revisit by reverting that PR, or by
  retraining with the relative block for the Vanguard only.
- **Detector shot times often land inside the shot, 20 to 30 ms late (2026-10-10).** On 27 % of
  the 667 shots in reviewed GO 3S fixtures, the shot's leading edge (the app's snap rule,
  `lib/peak-snap.snapToLeadingEdge`) sits more than 10 ms before the stored detector time; 3 %
  the other way. Plotted cases show a sharp burst starting 20 to 30 ms before the stored time,
  which sits mid-burst. Likely cause: librosa fires on a later loud part of the compressed burst,
  and the rise-foot walk back stops at the first dip inside it (`shot_detect`'s 20 ms rising
  guard). These are audited fixtures: the shots were kept at fit zoom, where 35 ms bars hid it.
  Splits between a late and an on-time shot are off by the difference.
- **A burst's first wavefront is often split from its body by a 1 to 2 ms dip (2026-10-10).**
  A first spike at 30 to 95 % of the peak, a dip below a quarter of it, then the main burst 5 to
  8 ms later. The rise-foot rule stopped at that dip until it learned to pass one dip under 3 ms;
  on headcam fixtures needing review that was 450 shots. Recognise it on a 1 ms zoom: two humps,
  the first narrow, less than 10 ms apart.

Implication: absolute spectral features drift within a stage on this camera. Features relative to
the stage's own earlier shots should hold up better; evaluate recall by shot index in string, not
only overall.

## Meta (Oakley) Vanguard (head)

- **The audio changed between April and September 2026.** In April (Blacksmith Handgun Open,
  `description=3Z`) every shot clipped (median peak 1.22 after AAC decode) with a long noisy tail.
  In September (Höstfinalen XI, `description=3Z37V01`) 27 % clip, median peak 0.96, and the tail is
  gated about 65 times lower; spectral flatness at the shot drops from 0.073 to 0.003. A firmware
  change is the likely cause; the version string is the evidence, not proof (2026-10-09).
- **Consequence.** Voter C trained without Höstfinalen scored none of its 180 Vanguard shots above
  the threshold (recall 0.000) while still ranking them well (AUC 0.946); the per-stage top-K mode
  from the expected round count is what kept detection working in the app. A stage without a round
  count, or the next firmware change, fails silently. Check older and newer Vanguard footage
  separately; the `description` tag tells them apart.
- **Fixed by stage-relative voter C (2026-10-09).** Held out by match, Höstfinalen Vanguard recall
  is 0.911 (precision 0.901) at the trained threshold, up from 0.000, with or without a round
  count. Blacksmith Handgun Open (April) stays at recall 0.988, precision 0.747.
- **Real stereo** (L/R correlation about 0.3 to 0.6 around shots). Own shots arrive more balanced
  and more coherent than other sounds, but as a voter C addition it caught nothing extra on 38 hard
  negatives (2026-10-09).
- **Detector shot times often sit in the silence before the shot (2026-10-10).** On 10 % of the
  347 shots in reviewed Vanguard fixtures the stored time is more than 10 ms before the leading
  edge (1 % the other way): plotted cases show silence at the stored time and the shot starting
  8 to 15 ms later. Where the rise has a quiet lead-in before the main burst, the app's snap rule
  can land on the main burst, a few ms after the lead-in; place those by eye at 1 ms zoom.
- Portrait 1200x1600 at about 60 fps. The promote route's comment says ffprobe exposes no make or
  model for these files; current ffprobe reads `model=Vanguard`.

## DJI Osmo Action 4 (head)

- Audio is too noisy to audit shots on (the user's call, Höstfinalen XI, Martin's stages 2 to 8
  were audited on a handheld angle instead). No fixtures yet.
- 2688x1512 at 59.94 fps.

## iPhone 17 Pro / Pro Max (hand)

- Real stereo in the AAC track; the 4 ch APAC spatial stream is invisible to our ffmpeg.
- Handheld means a squadmate films the shooter, so "own muzzle" cues (stereo direction, frame
  motion) do not apply. Inter-channel time differences are about one sample at 48 kHz.
- The bulk of the handheld corpus. Held-out handheld voter C is the strong cell (F1 about 0.96).
- **Leading edges are ambiguous on far angles (2026-10-10).** On reviewed handheld fixtures the
  stored detector time and the app's snap rule disagree by more than 10 ms on about 40 % of
  shots, in both directions (29 % with the snap earlier, 11 % later). Not plotted yet: reverb and
  the gun's mechanical sound arriving before the blast both blur the rise at a distance. The
  review queue lists the disagreeing shots (`scripts/fixture_review_inventory.py`).

## Samsung phone (hand)

- Real stereo AAC, variable frame rate. Secondary angle at Höstfinalen XI; no fixtures yet.

## Fixture coverage (2026-10-10)

Every secondary angle is now a fixture too, snapped from its stage's reviewed angle and marked
`needs_review` until audited on its own audio (#1363, `scripts/promote_secondary_angles.py`).

| Camera | Reviewed | Needs review | Matches |
|---|---|---|---|
| Insta360 GO 3S | 32 | 12 | Blacksmith 2026, Tallmilan 2026, Höstfinalen XI, Stockholm IPSC Open 2026, HFO Masters 2026 (snapped) |
| Meta Vanguard | 16 | 0 | Blacksmith Handgun Open 2026 (April firmware), Höstfinalen XI (September firmware) |
| DJI Osmo Action 4 | 0 | 8 | Höstfinalen XI (Martin; audited by ear on the handheld, placed on the DJI) |
| iPhone | about 98 | about 40 | most matches |
| Samsung | 0 | 11 | Höstfinalen XI, Stockholm IPSC Open 2026 |

Samsung phones write no make or model tag; the probe recognises them by their
`com.samsung.android.*` format tags (2026-10-10).

**Two angles' marked beeps disagree.** Snapping one angle's shots onto another by the reviewed beeps
alone missed by 50 to 200 ms on about a third of stage pairs (HFO Masters GO 3S against the handheld,
Höstfinalen and Stockholm phones against the headcam), which is beyond the 60 ms snap window. The
snap now first estimates the stage's constant lag from onset cross-correlation; the lag and the number
of shots that still landed at the window edge are in each fixture's promotion report and history
(2026-10-10).

## How these were measured

Throwaway probes against the fixture corpus and `build/sweeps/signals.parquet` (rebuilt with
`scripts/build_sweep_signals.py` after `scripts/build_ensemble_artifacts.py`, which writes the
held-out voter C scores it reads). "Held-out" means leave-one-match-out unless stated. The timbre
measures use the first 40 ms after each audited shot in the fixture WAV.
