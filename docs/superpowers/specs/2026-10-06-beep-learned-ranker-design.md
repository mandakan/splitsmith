# Beep detection: a learned candidate ranker (#949 step 2)

Status: design, approved in conversation 2026-10-06; this document awaits review.
Issue: #949. Step 1 (harness fixes, 127-fixture manifest, regression test) merged
as PR #1238; the Hilbert speedup as #1237. Step 3 (hand audit of the fixtures whose
beep is never a candidate) belongs to the user and is out of scope here.

## Goal

`beep_detect` finds the right run but ranks it wrong. Over the 127-fixture
calibration corpus (clip track, current main) top-1 is 65/127 (51.2 %), top-N
105/127 (82.7 %), handheld 43.8 % (105 of the 127 fixtures are handheld). The
ranking is a hand-written product, `tanh(silence/5) * tonal_factor * dur_factor`.
Replace it with a model fitted on the corpus that ranks the same candidates, and
make confidence a calibrated answer to "is the chosen beep right?".

Success: a large out-of-fold top-1 gain, measured on matches the model never saw,
with every lost fixture listed for the user to accept.

## Decisions taken in conversation

| question | decision |
|---|---|
| Confidence | The ranker sets it, through a calibrated confidence head (below). |
| Peak frequency as a feature | No. Timer-agnostic features only. |
| Ship bar | Net gain; every currently-right fixture that breaks is listed in the PR with its candidate table, for the user to accept. |
| Approach | Measure first. Logistic regression and GBDT on the same features; LR ships if it is within 2 pp of GBDT. |
| Models per camera class | One. `detect_beep` does not receive the mount and 21 headcam fixtures cannot carry a model of their own. |

## Why measure first

The issue's figures (88.3 % with three features learned, 97.3 % with nine) came
from a prototype that is not in the repository or on the dev host. They are
unverified. The first deliverable is a trainer that reproduces the experiment;
nothing ships unless it passes the gate.

## Constraints

- No new runtime dependency. `onnxruntime` is already one; scikit-learn stays in
  `[dev]` (training only), as for voter C.
- Detection stays a pure function: audio + config in, `BeepDetection` out.
- Run segmentation, the cutoff, the rise-foot onset, search windows and all 14
  call sites are unchanged.

## 1. Features

One pure function, `splitsmith.beep_features.candidate_features(...)`, computes a
candidate's feature vector. `detect_beep` calls it for every run it already
finds. The trainer gets its data by calling `detect_beep` and reading the
features back off the candidates, so training and runtime share one
implementation and cannot drift apart.

| # | feature | source |
|---|---|---|
| 1 | `log(silence_score)`: run peak over pre-window max | computed today |
| 2 | `tonal_ratio`: 2.2-3.5 kHz energy over 2-5 kHz | computed today |
| 3 | `duration_ms` of the run | computed today |
| 4 | `log(run_peak / noise_floor)` | new |
| 5 | `run_peak / global_peak` in the window | new |
| 6 | spectral flatness (Wiener) of the run, 2-5 kHz | new, one short FFT per run |
| 7 | spectral prominence: peak bin over median bin, 2-5 kHz | new, same FFT |

Excluded on purpose: peak frequency (would learn the corpus's timer brands), and
the candidate's position in the window (fixture clips are trimmed ~0.5 s headcam /
~5 s handheld before the beep; production slices derived per-stage windows, so
position would learn the trimming).

The trainer may drop a listed feature that does not help out-of-fold. Adding one
that is not listed is a revision of this spec.

`BeepCandidate` gains `features: BeepFeatures | None` (a Pydantic model with the
seven fields; default `None`, so stored project JSON loads unchanged, and older
clients ignore the field). It is what the trainer reads and what the audit
report shows.

## 2. Training, model choice, gate

`scripts/train_beep_ranker.py`:

1. **Data.** Run `detect_beep` over every manifest clip with every candidate kept
   (the trainer passes a large `top_n_candidates`; `0` keeps its existing meaning,
   winner only). A candidate is
   positive when its time is within the fixture's `tolerance_ms` of the labeled
   beep. Fixtures with no positive candidate (about 16) are left out of the
   ranker's training and counted as misses end to end.
2. **Groups.** A group is a match, `<match>-<year>` parsed from the stem.
   Evaluation is leave-one-match-out: no match is scored by a model that saw it.
3. **Scoring.** Within a clip, the candidate with the highest predicted
   probability is top-1.
4. **Models.** Logistic regression (standardised features, class-balanced) and a
   GBDT, on the same features. **LR ships if its out-of-fold top-1 is within 2 pp
   of the GBDT's**; otherwise the GBDT ships as ONNX.
5. **Report**, written to `tests/fixtures/beep_calibration/ranker_report.json`
   (beside `baseline.json`; it is an evaluation record, not a shipped artifact)
   and printed:
   - out-of-fold top-1 over reachable fixtures and end to end over all 127,
     overall and per tag (handheld, headcam, steel-prone);
   - top-N end to end;
   - per confidence bin (<0.5, 0.5-0.7, 0.7-0.95, >=0.95): count and precision,
     for the current detector and for the candidate model;
   - every fixture the current detector gets right at top-1 that the model gets
     wrong, with both rankings' candidate tables;
   - the fitted coefficients (LR) or sample predictions (ONNX), the feature list,
     and `model_version`.
6. **Gate.** Ship only if all hold, out of fold, end to end:
   - top-1 at least **61.2 %** (today's 51.2 % + 10 pp);
   - top-N at least today's **82.7 %**;
   - no wrong fixture in the >=0.95 bin, the auto-trust bin (today: 10 fixtures,
     all right). A wrong beep there skips human review, the worst failure this
     detector has, so the bar is zero, not a percentage.

   If the gate fails the work stops after step 2 of the plan: the report is
   committed, #949 updated with the numbers, nothing else ships.
7. **Final fit.** On all reachable fixtures; the report's figures stay the
   out-of-fold ones.

## 3. Runtime

- `detect_beep` computes features for every run, scores each with the ranker,
  sorts by probability. `BeepCandidate.score` is that probability. Onset and the
  surfaced top-N are unchanged.
- **LR wins:** coefficients, standardisation means and scales, and
  `model_version` live in a `BeepRankerConfig` on `BeepDetectConfig`
  (`config.py`), overridable by YAML like every other threshold.
- **GBDT wins:** `src/splitsmith/data/beep_ranker.onnx`, loaded through
  `ensemble/onnx_session` on CPU, session cached per process; config holds the
  file name and `model_version`. A missing or unloadable file raises: a shipped
  artifact that is absent is a packaging bug, not a reason to fall back silently.
- `BeepRankerConfig.ranker: "learned" | "heuristic"`, default `"learned"`.
  `"heuristic"` is today's formula and confidence, kept intact: the escape hatch
  and the regression reference.
- `BeepDetection` gains `ranker_version: str | None` (default `None`, so old audit
  JSON loads), recorded on every detection.
- Cost: one short FFT per candidate (typically 5-30) plus inference, a few
  milliseconds against the 0.3-3 s the envelopes take.

## 4. Confidence

Downstream reads confidence to answer "is the chosen beep right?": auto-trust at
`AutomationConfig.beep_low_confidence_threshold` (0.95), the review queue at
0.5-0.7.

- **Confidence head**: a logistic calibration over two inputs, the candidate's
  ranker logit and its margin over the best *other* candidate's logit. Fitted on
  the out-of-fold predictions of all 127 fixtures, labeled by whether that
  fixture's out-of-fold top-1 was right. The unreachable fixtures are included on
  purpose: there the winner is always wrong, which teaches the head to stay low
  when nothing in the clip looks like a beep. Two coefficients and an intercept,
  in `BeepRankerConfig` whichever ranker wins.
- Every candidate's `confidence` is the head on its own logit and margin. The
  winner's is the detection's confidence; a runner-up's margin is negative, so
  its confidence is low.
- `candidate_confidence` and its constants remain only for `"heuristic"`.
- Thresholds do not change unless the report shows a reason. If one should move,
  the PR proposes it on its own line for the user to decide.
- `BeepCandidate.confidence`'s docstring drops the "~95 %" history and points at
  `ranker_report.json` for current bin figures.

## 5. Tests

- **Features**: synthetic tone versus noise burst (flatness low for the tone and
  high for noise, prominence the reverse); ratios unchanged under x10 gain.
- **No skew**: the features `detect_beep` attaches equal a direct
  `candidate_features` call on the same run.
- **Regression set**: `test_beep_regression.py` keeps its job; `baseline.json` is
  regenerated from the learned ranker, so newly fixed fixtures are pinned.
- **Heuristic stays alive**: with `ranker: "heuristic"`, a handful of fixtures
  reproduce today's exact top-1 time.
- **Confidence**: the shipped head, applied to the report's recorded out-of-fold
  inputs, reproduces the report's >=0.95 bin (its count, and no wrong fixture in it). A hand edit to the
  coefficients fails it.
- **Model integrity**: LR, config coefficients equal the report's; ONNX, the
  shipped file reproduces the report's sample predictions.
- Every new test is shown failing against the pre-change code (ranker disabled
  or reverted) before merge.

## 6. Plan of work

Each step is its own PR and mergeable alone.

1. `candidate_features`, `BeepFeatures`, `BeepCandidate.features`.
   Behaviour unchanged: features computed and exposed, ranking untouched.
2. The trainer and the committed report. Stop here if the gate fails.
3. The ranker and confidence head in `detect_beep`, `ranker_version`, the
   regenerated baseline, the losses listed in the PR, and a review pass (CLAUDE.md
   requires one for detection changes).
4. The user runs the eval over the full-track WAVs on the Mac and records the
   result on the step-3 PR before release. Production slices wider windows than
   the fixture clips, so the corpus figures are a floor.

## Out of scope

- The ~16 fixtures whose beep never becomes a candidate (#949 defect 2, step 3).
- The sub-run split the issue measured at +0.8 pp.
- `cross_align.py`'s Hilbert call (same slow-length pattern as #1237).
