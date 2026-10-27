# Finding: shot detection needs no model download; CLAP buys about one point on head cameras

**Date:** 2026-10-26
**Spec:** `docs/superpowers/specs/2026-10-26-thin-detection-design.md`
**Corpus:** all 364 fixtures (`fixture_stems()`), 13 not yet reviewed; 20,093 candidates, 7,830 positives. Headcam 8,015 candidates / 2,314 positives from 9 matches; handheld 12,078 / 5,516 from 11. Voter E off (no source videos on the build host, as before).

## TL;DR

- **PANN is dead weight.** Its `gunshot_prob` had GBDT importance 0.0025, and removing it improved headcam in every configuration the spike tried. It is retired: the 327 MB download, the runtime, the caches and the manifest entry are gone.
- **Handheld needs no learned audio model.** Hand-crafted features alone match the old columns: event F1 0.979 (`base`) against 0.981 (`clap`).
- **Headcam was data-starved, not feature-starved.** The shipped model (built 2026-10-09) trained its headcam class on 37 fixtures from 4 matches; the corpus held 108 from 9. Rebuilt on all of them, the no-download `base` set reaches event F1 0.924 on headcam, and `clap` 0.936.
- So detection now runs the `base` set by default (no download), and the 120 MB CLAP model is an opt-in second set, `clap`. Hosted runs `clap`.

## The spike (2026-10-26, before the build)

Leave one match out, one GBDT per camera class, the stage decoder fitted on those scores, event P/R/F1 at 75 ms. Built by a throwaway script over the build's own universe.

| Headcam (108 fixtures, 9 matches, 2,314 shots) | downloads | F1 | FP | FN |
|---|---|---|---|---|
| hand features only | 0 | 0.927 | 132 | 195 |
| hand + CLAP, no PANN (same 104 fixtures) | 120 MB | 0.936 | 112 | 178 |

| Handheld (68 fixtures, 5 matches) | downloads | F1 |
|---|---|---|
| the old columns | 447 MB | 0.971 |
| no PANN | 120 MB | 0.970 |
| hand features only | 0 | 0.974 |

On the three headcam matches the shipped model knew, hand-only scored 0.59; on all nine it scores 0.93.

## The build and its gate

The real build (`scripts/build_ensemble_artifacts.py`) against a baseline built from the unchanged script on the same rows (the old columns, PANN included).

Candidate level, voter C held out by match, each build at its own threshold for 95 % recall:

| class | set | F1 | old columns | delta |
|---|---|---|---|---|
| handheld | `clap` | 0.9703 | 0.9703 | +0.0000 |
| handheld | `base` | 0.9701 | 0.9703 | -0.0002 |
| headcam | `clap` | 0.9224 | 0.9218 | +0.0006 |
| headcam | `base` | 0.9085 | 0.9218 | -0.0133 |

Event level at 75 ms, the whole-stage decoder out of fold by match. The old build fitted one decoder over both classes (F1 0.9673, 189 FP, 322 FN); the new one fits one per class per set:

| class | set | F1 | FP | FN | floor |
|---|---|---|---|---|---|
| handheld | `clap` | 0.9808 | 92 | 120 | |
| handheld | `base` | 0.9791 | 113 | 118 | 0.97 |
| headcam | `clap` | 0.9358 | 152 | 149 | |
| headcam | `base` | 0.9235 | 139 | 214 | 0.92 |

The gate holds. `clap` is within 0.005 of the old columns in both classes, and no match drops by more than 0.02 at candidate level. The largest drop is Satra headcam at -0.007; HFO Masters, Hostfinalen and Tallmilan handheld and Blacksmith and Tallmilan headcam drop by 0.002 to 0.004. `base` clears both event floors and is within 0.03 of `clap` on every match at event level (below).

At candidate level, before the decoder, `base` headcam is 0.046 under `clap` on HFO Masters (0.855 against 0.901) and 0.025 to 0.026 under it on Hostfinalen and Stockholm. The decoder closes most of that.

### Per match, event level

Recomputed from the shipped calibration and `build/ensemble_heldout/voter_c_oof.parquet` with the build's own out-of-fold loop (totals equal each decoder's `fit_report`):

| class | match | `clap` F1 | `base` F1 | base - clap | `clap` FP / FN | `base` FP / FN |
|---|---|---|---|---|---|---|
| headcam | blacksmith-handgun-open-2026 | 0.961 | 0.947 | -0.014 | 11 / 14 | 8 / 25 |
| headcam | ess-black-handgun-2026 | 0.957 | 0.934 | -0.024 | 12 / 13 | 12 / 26 |
| headcam | hfo-masters-2026 | 0.906 | 0.888 | -0.018 | 19 / 26 | 16 / 36 |
| headcam | hostfinalen-xi-2026 | 0.924 | 0.909 | -0.015 | 60 / 28 | 64 / 41 |
| headcam | hugelsta-six-station-fall-2026 | 0.978 | 0.973 | -0.005 | 2 / 3 | 1 / 5 |
| headcam | little-trouble-in-big-satra-2026 | 0.801 | 0.839 | +0.038 | 14 / 38 | 5 / 36 |
| headcam | oden-cup-2026 | 0.966 | 0.962 | -0.003 | 10 / 12 | 10 / 14 |
| headcam | stockholm-ipsc-open-2026 | 0.960 | 0.935 | -0.025 | 11 / 9 | 11 / 21 |
| headcam | tallmilan-2026 | 0.904 | 0.885 | -0.018 | 13 / 6 | 12 / 10 |
| handheld | black-handgun-2023 | 0.962 | 0.953 | -0.009 | 22 / 18 | 32 / 18 |
| handheld | blacksmith-handgun-open-2026 | 0.953 | 0.953 | -0.000 | 28 / 5 | 26 / 7 |
| handheld | bofors-bombardment-2026 | 0.994 | 0.995 | +0.001 | 3 / 2 | 2 / 2 |
| handheld | ess-black-handgun-2026 | 0.975 | 0.970 | -0.005 | 1 / 17 | 6 / 16 |
| handheld | hfo-masters-2026 | 0.984 | 0.982 | -0.002 | 10 / 24 | 15 / 23 |
| handheld | hostfinalen-xi-2026 | 0.979 | 0.979 | +0.000 | 3 / 26 | 3 / 26 |
| handheld | hugelsta-six-station-fall-2026 | 0.975 | 0.976 | +0.000 | 7 / 5 | 8 / 4 |
| handheld | oden-cup-2026 | 0.995 | 0.995 | +0.000 | 1 / 2 | 1 / 2 |
| handheld | stockholm-ipsc-open-2026 | 0.983 | 0.982 | -0.001 | 15 / 17 | 17 / 17 |
| handheld | tallmilan-2026 | 0.993 | 0.994 | +0.001 | 2 / 4 | 2 / 3 |
| handheld | vads-easter-shoot-gotta-go-fast | 1.000 | 0.998 | -0.002 | 0 / 0 | 1 / 0 |

On head cameras `base` misses more (214 against 149) and fires a little less (139 against 152). Satra is the one match `base` does better on, and Satra is where the GO 3S misses and labels are unchecked.

### Reviewed-only

`--reviewed-only` (351 fixtures; the 13 left out are angles snapped from another camera and not yet reviewed):

| set | class | candidate F1, all | candidate F1, reviewed | event F1, all | event F1, reviewed |
|---|---|---|---|---|---|
| `clap` | handheld | 0.9703 | 0.9701 | 0.9808 | 0.9793 |
| `clap` | headcam | 0.9224 | 0.9291 | 0.9358 | 0.9389 |
| `base` | handheld | 0.9701 | 0.9699 | 0.9791 | 0.9783 |
| `base` | headcam | 0.9085 | 0.9144 | 0.9235 | 0.9293 |

The 13 unreviewed fixtures are 12 handheld iPhone angles from VADS and one Oden Cup Vanguard angle, so headcam differs by a single stage: the half point it gains without that stage is that stage, not a pattern. Handheld moves by under 0.002.

## Errors by camera (spike, headcam, `clap`-like columns)

| camera | fixtures | false positives per 100 shots | misses per 100 shots |
|---|---|---|---|
| DJI Osmo Action 4 | 8 (one shooter, Hostfinalen) | 24 | -- |
| Insta360 GO 3S | 62 | 3 to 5 | 11 (Satra, HFO Masters) |
| Meta Vanguard | 38 | 3 to 5 | -- |

Most DJI false positives are not echoes: the median sits 0.7 s from the nearest true shot. Recorded in `docs/cameras.md`; not fixed here.
