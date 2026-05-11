# Finding: voter D (PANN gunshot) folded into voter C as a feature

**Date:** 2026-05-11
**PRs:** ensemble: fold + ensemble: drop voter D
**Sweep run_id:** `2026-05-11T08-25-03Z_f62433e_baseline_default`

## TL;DR

The PANN ``Gunshot, gunfire`` class probability is now a column on
voter C's GBDT input vector rather than an independent vote.
The 4-voter (A+B+C+D) ensemble collapses to a 3-voter (A+B+C)
ensemble with ``consensus`` defaulting to ``2`` instead of ``3``.
Aggregate F1 is **byte-identical at 0.9735269** on the 30-fixture /
1713-candidate corpus (570 TPs, 25 FPs, 6 FNs).

## Why this works

The leverage map established that with ``c_required=True`` (production
default) voter D's threshold was a dead axis:

* `2026-05-11_voter_abd_have_no_leverage.md` -- voter D's threshold
  offset moved F1 by < 0.001 across the whole sweep range.
* `2026-05-11_consensus_dead_under_c_required.md` -- consensus did
  nothing under c_required because C-veto already gated every kept
  candidate.

So the kept set under (A+B+C+D, consensus=3, c_required=True) is
the same as under (A+B+C, consensus=2, c_required=True): both
amount to "voter C says yes AND at least one of voter A / B
agrees". The corpus confirms this byte-identically.

## What changed structurally

| before | after |
|---|---|
| 4 voters (A, B, C, D), consensus default 3 | 3 voters (A, B, C), consensus default 2 |
| ``voter_c_feature_dim = 30`` | ``voter_c_feature_dim = 31`` (gunshot_prob added) |
| voter D = ``gunshot_prob >= calibrated threshold`` | gunshot_prob = GBDT feature, no separate vote |
| EnsembleCandidate.vote_d (informational) | dropped |
| ClassThresholds.voter_d_threshold | dropped |
| e_audio_strong_min_votes default 4 (A+B+C+D unanimous) | default 3 (A+B+C unanimous) |
| sweep grids: voter_d_threshold, voter_d_threshold_offset | grids deleted |

GBDT feature importance picked up ``gunshot_prob`` at **0.0025** --
non-zero but small, consistent with the gunshot signal being mostly
redundant with the existing RMS / spectral / CLAP columns voter C
already trained on. Per-class CV metrics dipped marginally
(handheld F1 0.9621 -> 0.9592, headcam F1 0.9077 -> 0.9041) -- a
normal regularization effect from adding a noisy correlated
feature -- but the production operating point's kept set is
unchanged.

## What this unlocks

The dashboard arc closed with an open question: now that voters
A/B/D have no leverage, do we need them as separate voters at all?
The answer for D is "no, fold it" -- this PR. For B (CLAP diff)
the answer is the same in principle, but unlike D's single scalar,
B's underlying CLAP-similarity features already live in voter C's
feature vector; B's vote is essentially a thresholded view of
columns C already sees. Future PR.

The bigger payoff is what gets easier for the next round of
precision work:

* **Trying a new audio model** (AST, BEATs, wav2vec2 embeddings,
  small CNN logit over the candidate window) is now "add a feature
  column to ``voter_c_feature_matrix`` + retrain". No new voter
  scaffolding, no separate threshold calibration, no new consensus
  interaction to characterise on the dashboard.
* **Temporal context features** (inter-candidate gap, local
  density, prior-candidate score) become tractable -- the per-
  candidate voter framework couldn't express them.

## Reproduce

```bash
uv run python scripts/build_ensemble_artifacts.py
uv run python scripts/build_sweep_signals.py --skip-voter-e
uv run python scripts/run_sweep.py --grid /tmp/baseline_default.yaml --append
# expect aggregate F1 = 0.9735269 on the 30-fixture corpus
```
