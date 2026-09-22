# Finding: the honest baseline is 0.959 grouped, 0.942 across matches

**Date:** 2026-09-22
**Issue:** #1045 (prerequisite for every experiment in #1053)
**Sweep run_ids:** `*_heldout-shipped`, `*_heldout-stratified`, `*_heldout-grouped`, `*_heldout-lomo`
**Corpus:** 124 audited fixtures (126 minus the 2 wrong-clip ones), 6 matches, 49 stages, 6077 candidates, 2603 positives. Voter E off (source videos unreachable, same as the shipped build).

## TL;DR

Every ensemble figure reported so far scored the shipped voter C model on the candidates it was fitted on. Replayed at the shipped operating point (every threshold, consensus and slack unchanged) with voter C's probabilities held out instead, event F1 at 75 ms falls from **0.979** to **0.959** when every stage is held out, and to **0.942** when a whole match is held out. Headcam takes the hit: **0.971 -> 0.912 -> 0.856**.

These two numbers, grouped **0.959** and leave-one-match-out **0.942**, are the bar for every experiment in #1053. Handheld and headcam separately:

| voter C scores | class | P | R | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| shipped (in-sample) | all | 0.964 | 0.995 | **0.979** | 2589 | 96 | 14 |
| | handheld | 0.967 | 0.995 | 0.981 | 2181 | 74 | 12 |
| | headcam | 0.949 | 0.995 | 0.971 | 408 | 22 | 2 |
| stratified (by candidate) | all | 0.941 | 0.981 | 0.960 | 2553 | 160 | 50 |
| | handheld | 0.952 | 0.986 | 0.969 | 2162 | 109 | 31 |
| | headcam | 0.885 | 0.954 | 0.918 | 391 | 51 | 19 |
| **grouped (by match + stage)** | all | 0.940 | 0.978 | **0.959** | 2545 | 162 | 58 |
| | handheld | 0.951 | 0.984 | 0.967 | 2158 | 110 | 35 |
| | headcam | 0.882 | 0.944 | 0.912 | 387 | 52 | 23 |
| **leave one match out** | all | 0.915 | 0.972 | **0.942** | 2529 | 235 | 74 |
| | handheld | 0.936 | 0.983 | 0.959 | 2156 | 147 | 37 |
| | headcam | 0.809 | 0.910 | **0.856** | 373 | 88 | 37 |

## What the gap is made of

The issue expected the by-candidate split to be the leak: the same shots recorded by two cameras landing in different folds. On this corpus that part is small. The by-candidate and grouped rows differ by 0.001 overall and 0.006 on headcam. Almost all the optimism comes from two other places:

1. **Scoring the fitted model on its own training data** (0.979 -> 0.960). Every sweep report so far has done this, including the 0.971 in `latest_report.md` (2026-05-11, 30 fixtures). It is not a split choice; `build_sweep_signals.py` simply had no held-out probabilities to replay.
2. **A new match** (0.959 -> 0.942), concentrated in headcam. Headcam has 21 stages from only 3 matches, so leaving one out removes a third of its training data and a whole range's acoustics.

Leaving each match out in turn, per class:

| class | held-out match | positives | P | R | F1 | FP | FN |
|---|---|---|---|---|---|---|---|
| handheld | blacksmith-2026 | 137 | 0.924 | 0.971 | 0.947 | 11 | 4 |
| handheld | blacksmith-handgun-open-2026 | 170 | 0.932 | 0.965 | 0.948 | 12 | 6 |
| handheld | bofors-bombardment-2026 | 383 | 0.962 | 0.995 | 0.978 | 15 | 2 |
| handheld | hfo-masters-2026 | 1024 | 0.918 | 0.984 | 0.950 | 90 | 16 |
| handheld | tallmilan-2026 | 196 | 0.955 | 0.969 | 0.962 | 9 | 6 |
| handheld | vads-easter-shoot-gotta-go-fast | 283 | 0.966 | 0.989 | 0.977 | 10 | 3 |
| headcam | blacksmith-2026 | 148 | 0.801 | 0.845 | 0.822 | 31 | 23 |
| headcam | blacksmith-handgun-open-2026 | 167 | 0.801 | 0.940 | 0.865 | 39 | 10 |
| headcam | tallmilan-2026 | 95 | 0.835 | 0.958 | 0.892 | 18 | 4 |

No handheld match is an outlier. The headcam picture is one of too few matches, not of one bad match.

## Voter C alone, candidate level

From `build/ensemble_heldout/report.json`. "Own threshold" is what re-calibrating voter C's 95 % recall target on that split would ship.

| class | split | P / R / F1 at shipped threshold | own threshold | P / R / F1 at own threshold | FP at own |
|---|---|---|---|---|---|
| handheld | stratified | 0.991 / 0.950 / 0.970 | 0.916 (shipped) | same | 19 |
| handheld | grouped | 0.990 / 0.938 / 0.964 | 0.880 | 0.988 / 0.950 / 0.969 | 26 |
| handheld | leave one match out | 0.981 / 0.940 / 0.960 | 0.886 | 0.978 / 0.950 / 0.964 | 47 |
| headcam | stratified | 0.886 / 0.951 / 0.918 | 0.198 (shipped) | same | 50 |
| headcam | grouped | 0.866 / 0.929 / 0.897 | 0.084 | 0.786 / 0.951 / 0.861 | 106 |
| headcam | leave one match out | 0.790 / 0.915 / 0.848 | 0.034 | 0.681 / 0.951 / 0.794 | 183 |

## Decision: the shipped threshold stays

The handheld threshold barely moves under a grouped split (0.916 -> 0.880, +7 FP), so there is nothing to gain. For headcam, holding 95 % recall on an unseen match would drop the threshold to 0.034 and more than triple voter C's false positives. That trade runs against the project's "under-detect rather than invent shots" rule, and it would only paper over a corpus problem. The shipped model and thresholds are unchanged by this work, and the rebuild reproduced them bit for bit: all six per-class A / B / C thresholds and the by-candidate figures match `ensemble_calibration.json` from 2026-08-17.

The lever for headcam is more headcam matches (#1050) and better inputs (#1047), measured against the 0.856 cell above.

## Protocol for the rest of #1053

1. `uv run python scripts/build_ensemble_artifacts.py --no-voter-e` writes `build/ensemble_heldout/report.json` and `voter_c_oof.parquet` next to the usual artifacts. Restore `src/splitsmith/data` and `tests/data` from git afterwards unless the experiment is meant to ship.
2. `uv run python scripts/build_sweep_signals.py --skip-voter-e` joins the held-out columns onto the sweep (`score_c_stratified` / `_grouped` / `_lomo`) and refuses a stale file whose candidates sit at different times.
3. `uv run python scripts/run_sweep.py --score-c grouped` (and `--score-c lomo`). `run_sweep` refuses a held-out column with any NaN, and every report now names its voter C source.
4. Compare against this note's grouped and leave-one-match-out rows, per class. `--score-c shipped` is in-sample and is not a result.

## Reproduction notes

The detection code segfaults under this machine's Homebrew Python 3.14 (see the host notes), so this run used a separate `uv sync --python 3.11` environment matching CI. `extract_clap_features.py` and `extract_audio_embeddings.py` took about 3 minutes each on an M-series Mac; the build about 2 minutes.
