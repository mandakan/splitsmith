---
paths:
  - "src/splitsmith/beep/**"
  - "src/splitsmith/cross_align.py"
  - "src/splitsmith/lab/model_map.py"
  - "tests/fixtures/*.json"
  - "tests/test_train_beep_ranker.py"
  - "src/splitsmith/ui/beep_tone_check.py"
  - "tests/fixtures/beep_calibration/**"
  - "scripts/*beep*.py"
  - "tests/test_beep*.py"
  - "docs/METHODOLOGY.md"
  - "docs/cameras.md"
---

# Beep detection

Read `docs/cameras.md` before tuning a threshold; add to it, dated, whenever a
measurement shows something new about a camera.

## The ranker

- Beep detection runs inside per-stage derived search windows for multi-stage
  single-take videos (ffmpeg extracts the window via `-ss`/`-t`; results are
  offset back to source-absolute).
- Candidates are ranked by a logistic regression over seven per-run features.
  **`beep_features.candidate_features` is the one implementation both runtime
  and trainer use.** Confidence is a calibrated head over (logit, margin).
- The numbers are `BeepRankerConfig` defaults in `config.py`, pasted from
  `ranker_report.json`'s `models.lr.final_fit`; `tests/test_beep_ranker_config.py`
  fails on any drift.
- **Retraining after adding fixtures:** rebuild the manifest, run
  `scripts/train_beep_ranker.py`, paste the new `final_fit`, regenerate
  `baseline.json` (`scripts/eval_beep_detector.py --track clip --json ...`),
  and list every newly wrong fixture in the PR.
- `ranker: heuristic` is the old hand-written product, kept as the escape hatch;
  `ranker: heuristic` with `tone_onset: false` is the old product exactly
  (`baseline_heuristic.json`).
- Remaining misses are mostly fixtures where the beep is never a candidate (run
  merging, late onsets); every confident mistake so far is one of those. Those
  were edge errors, not ranking errors.

## Candidate time is the tone's start

Every candidate's time is the timer tone's start (`beep_tone.tone_onset`,
`BeepDetectConfig.tone_onset`, default on; `docs/METHODOLOGY.md` step 4). The
ranker's remaining errors were edges, not rankings: it chose the tone's run and
the rise foot landed off the tone. So don't "unify" the beep time onto
`rise_foot`, which shots use.

**Score a beep change against labels checked on a spectrogram.** Many corpus
labels were once confirmed from the old edge's picks, so a detector scored
against them looked right where it was wrong; those labels were moved onto their
tones, each move recorded in its audit doc's `audit_events` as
`beep_moved_to_tone`. **When a fixture's beep moves, re-extract its CLAP / PANN
caches**: its candidates move with it, which the caches' WAV stamp cannot see.

## A secondary camera's beep from the primary's

`beep_cross.place_beep`: the primary clip's onset pattern from its beep through
the stage is slid over the secondary's (`stage_match`; contrast >=
`MIN_CONTRAST` 1.2 is confident), then where the primary timer's own frequency
starts near it (`line_onset_near`), kept only within 60 ms of the shots'
projection. `scripts/eval_beep_from_angle.py` measures it over every corpus
stage with reviewed beeps on two or more angles. The older `cross_align`
envelope landmark was seconds off on many pairs: don't use it to place a
secondary's beep. It is still live for other jobs (fixture promotion through
`lab/promote.py`).

In the beep job (`_try_align_secondary_to_primary` in `ui/server.py`; the root
CLAUDE.md repeats the 60 ms rule, since that file is unscoped):

- it is the proposed beep when in-stream detection finds none;
- when confident it is `StageVideo.beep_aligned_time` (a "from primary"
  candidate in beep review);
- **an in-stream beep that disagrees with it by more than 60 ms is never
  auto-trusted**;
- confirming the primary's beep re-runs detection for every unconfirmed, not
  hand-set secondary of the stage (`_replace_secondary_beeps`).
