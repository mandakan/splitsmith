# Precision-limit notes

Tracks the failure modes that currently cap candidate-filter precision.
Listed roughly in order of how much headroom they represent. Each entry
documents the *physical* problem so we can pick the right feature/algorithm
later instead of throwing more GBDT at it.

## Current state (2026-05-01, 8 fixtures, 153 positives)

| Stage | Recall | Precision (3-of-4) | Notes |
|---|---|---|---|
| 4-of-4 consensus, overall | 95.4 % | 72.3 % | strictest |
| 3-of-4 consensus, overall | 100 % | 29.9 % | default UI filter |
| Voter C alone (GBDT, 95 % recall) | 95.4 % | 69.5 % | StratifiedKFold |
| Voter C, LOFO | 95.4 % | 56.4 % | cross-fixture honest |

Dream target (user, 2026-05-01): **100 % recall, 80 % precision**. We are
roughly at the precision the user labels as "every little progress helps the
production UI"; not yet at the dream.

---

## 1. Cross-bay shots vs. AGC-ducked local shots

**Where it bites:** `tallmilan-2026-stage5` (39.5 % LOFO precision -- worst
fixture), `tallmilan-stage2` (38.7 %).

**Physics:** a cross-bay shot on a neighbouring stage has the same impulsive
shape as a local shot (sharp pressure spike, reverb tail) but lower amplitude.
A local shot recorded while the camera's AGC is ducked (after a loud burst)
has the same lower amplitude. From a single-channel envelope/spectral
perspective they are nearly indistinguishable.

**Why current features cannot fix it:**
* `peak_amp`, `confidence` -- both classes look similar.
* `tail_amp` -- both have tails (cross-bay is just smaller).
* `mr_ratio_1_20`, `mr_ratio_5_20` -- impulsive shape is the same.
* PANN gunshot_prob -- pretrained on AudioSet, fires on both.
* CLAP shot prompts -- same.

**What would actually work:**
* **Stereo TDOA / inter-channel level difference.** The Insta360 GO 3S
  records mono, so this requires either a hardware change or post-processing
  to recover any directional cue from the body-worn mic's HRTF (low odds).
* **Cadence pattern matching.** A local shooter has a typical inter-shot
  rhythm (0.15 - 0.40 s splits for accurate-paced); cross-bay shots come at
  a different cadence and are usually clustered in a single bay's stage
  start, not spread out. A "burst extractor" that segments candidates into
  cadence-coherent groups and votes per group could downweight cross-bay
  bursts. Risk: collapses on transition stages with deliberate pauses.
* **Apriori shot count from SSI.** If the user supplies `--expected-rounds N`
  and we have N+k candidates with k cross-bay-class candidates near the end,
  preferring the cadence-coherent N is a soft lever. Already a CLI flag,
  not yet wired into the cross-bay heuristic.

## 2. Beep auto-detection picks a steel ring on AGC'd recordings

**Where it bites:** `tallmilan-2026-stage6` and any stage where a clean
in-stage steel transient outscores the actual beep on
silence-preference + amplitude-floor scoring.

**Physics:** the IPSC start beep is ~2.5-4 kHz, ~300 ms duration, preceded
by ~3-5 s of "Are you ready / Stand by" (mostly speech below 1 kHz, plus
silence). On a recording with strong AGC ducking after the beep, a later
steel ring inside the stage can have:
* similar pre-window quietness (long pause after a slow string of shots), AND
* higher in-band peak amplitude than the beep itself (which was attenuated by
  the limiter).

The silence-preference score `peak / (pre_window_mean + eps)` then picks the
steel ring.

**Status:** documented in `src/splitsmith/beep_detect.py` docstring
("Known failure mode -> future production UX"). User-facing workaround:
`--beep-time` override.

**What would actually work:**
* **Surface top-N beep candidates in the audit UI before trim** (planned;
  see beep_detect docstring).
* **Spectral template match.** The beep is a *steady tone* in [2.5, 4] kHz
  for ~300 ms; steel rings are broadband transients with a fast HF roll-off.
  A within-band spectral flatness / harmonicity feature would split them.
  Cheap to add to the candidate-scoring step in `beep_detect`.
* **Pre-window speech detection.** "Are you ready / Stand by" produces a
  detectable speech-band signature. Scoring "candidate has 1-3 s of speech-
  band activity in its pre-window" boosts the real beep without confusing
  it with stage transients.

## 3. Voter C overfits per-fixture noise without cross-fixture validation

**Where it bites:** the gap between StratifiedKFold (+10.2 pp from MR
features) and LOFO (+2.9 pp) is roughly 7 pp of per-fixture overfitting.
Adding any new feature without LOFO validation risks the same gap.

**Cause:** 153 positives across 8 fixtures = ~19 positives/fixture on
average. The GBDT picks up per-fixture quirks (mic gain, room reverb,
ambient profile) when it can see candidates from the same fixture in
training. Each new feature gives it more knobs to overfit on.

**What would actually work:**
* **More fixtures.** This is the bottleneck. Every new audited fixture has
  paid back in either improving precision or exposing a regression.
* **Always validate new features with LOFO**, not just StratifiedKFold.
  Codified in `scripts/analyze_negatives.py --no-mr` for ablation.
* **Per-shooter normalisation.** When we have multiple shooters, normalise
  features by per-shooter median peak/RMS before the GBDT sees them.

## 4a-resolved (2026-05-01): second-pass refinement in ``shot_refine``

History:
1. First attempt was a reverb-chain re-anchor in
   ``shot_detect._leading_edge`` (commit 790191a). It DID fix stage-3 cand
   #35 but tanked voter C precision 72.1 % -> 60.2 % and 4-of-4 ensemble
   74.9 % -> 62.9 % because it also shifted false-positive candidates'
   positions, pulling their audio features (peak_amp, attack, tail_amp, MR
   ratios) toward more shot-like values and dissolving the
   positive/negative separation. Reverted in 428f407.
2. Resolved by moving the re-anchor logic into a NEW module
   ``src/splitsmith/shot_refine.py`` that runs AFTER voter filtering /
   user audit. It updates ONLY the timestamp; nothing flows back into the
   voter feature distribution. The candidate generator stays narrow and
   stable; voter precision is preserved.

The module exposes ``refine_shot_time(audio, sr, approx_time, config)
-> RefinedShot`` and supports two methods:

* ``"envelope"`` (default): wide broadband peak + 5 % rise-foot
  backtrack, gated on ``wide_peak / local_peak >= reanchor_ratio`` so
  clean shots fall through unchanged. Recovers stage-3 cand #35 from
  144 ms drift to 1.2 ms drift; leaves the other 153 audited shots
  untouched.
* ``"aic"``: Akaike picker on bandpassed raw waveform. Sub-ms accurate
  on isolated transients but reports low confidence on busy reverb
  backgrounds (correct rejection in those cases).

Wiring: not yet called from the production CLI -- the audit UI / CSV
generator should call this after the user confirms candidates. Eval
script ``scripts/eval_refinement.py`` measures timing accuracy across
fixtures.

## 4a. Candidate generator anchors on reverb peaks instead of onset

**Where it bites:** stage-shots-blacksmith-2026-stage3 candidate #35 -- the
detector placed its anchor 144 ms after the true muzzle blast onset, on a
reverb peak. The user's audit nudged the timestamp back to the correct
position, but the candidate's *audio features* still belong to the reverb
peak (low PANN gunshot probability, low CLAP shot-similarity, low detector
confidence, low attack). Surfaced by ``eval_ensemble.py`` as a "candidate-
generator miss".

**Why it can't be papered over with feature engineering:** including such
candidates as positives in voter calibration drags the auto-calibrated
thresholds down (voters require 100 % recall, so they lower their threshold
to admit the reverb-feature positive) and tanks precision across the board.
4-of-4 dropped from 74.9 % to 27.9 % when this was tried. We now exclude
linked-but-shifted audits from positives and report them separately so
recall accounting is honest without poisoning the feature distribution.

**Real fix is in the candidate generator, not the voter layer:**
* Tighten the rise-foot backtracking in ``shot_detect`` so it walks past
  reverb peaks to the true onset.
* OR, run an AIC picker / matched filter on the raw waveform inside
  ~30 ms windows around each candidate to refine the anchor (this is the
  same machinery proposed for the confirmation-time second pass in
  GitHub issue #7, but applied at candidate-generation time instead).

## 4. Candidate timing precision is capped by 10 ms envelope smoothing

**Where it bites:** AGC-ducked shots whose envelope rise is slow can have
the rise-foot timestamp drift 5-15 ms from the true muzzle blast onset.
Splits between consecutive shots cancel the drift if both are clean; mixed
drift (one clean, one AGC'd) does not cancel.

**Status:** open as GitHub issue #7 (confirmation-time re-timing pass with
AIC picker / matched filter on user-confirmed candidates). Defer until a
concrete timing complaint surfaces in audits.

## 5. Wind / handling false positives with sustained tails

**Where it bites:** outdoor recordings with gusts can produce candidates
with a tail amplitude similar to gunshot tails. `tail_amp` does not split
these.

**Mitigation idea:** low-frequency band ratio (0-200 Hz vs. 2-8 kHz) over
a 200 ms window after the candidate. Wind has dominant low-frequency
content; muzzle blast tails are broadband. Adds 1 dim to the GBDT.

**Priority:** low until we see a fixture where wind dominates the
disagreement set.

---

## Disciplines for any future feature

1. Per-feature pos/neg medians **first** (free sanity check; weak medians
   can still help nonlinearly via GBDT but are higher risk).
2. **StratifiedKFold AND LOFO** in `analyze_negatives.py`. Reject any
   feature where LOFO regresses precision on more than 2 of 8 fixtures.
3. Recall must hold on every fixture, not just the average.
4. Document the *physical* mechanism in the feature comment, not just
   "improves precision". If you cannot explain why a feature should work,
   the GBDT is overfitting.
