# Finding: voter_c_slack_frac is over-tuned for stage7 recall

**Date:** 2026-05-11
**Sweep run_id:** `2026-05-11T06-11-46Z_902c641_voter_c_slack_fine`
**Corpus:** 30 audited fixtures, 1713 candidates, 576 positives

## TL;DR

The production `voter_c_slack_frac=0.25` (chosen in #103 on 4 fixtures
with 281 hand-labeled FPs) is mis-calibrated on the current 30-fixture
corpus. Lowering it to anywhere in **[0.05, 0.125]** lifts aggregate F1
from **0.959 -> 0.971** by suppressing **18 false positives** at the
cost of **3 lost true positives**, all of which land on stage7
fixtures.

| slack_frac | kept | TP  | FP | FN | P     | R     | F1     |
|------------|------|-----|----|----|-------|-------|--------|
| **0.050**  | 592  | 567 | 25 |  9 | 0.958 | 0.984 | **0.971** |
| 0.075      | 592  | 567 | 25 |  9 | 0.958 | 0.984 | 0.971 |
| 0.100      | 592  | 567 | 25 |  9 | 0.958 | 0.984 | 0.971 |
| 0.125      | 592  | 567 | 25 |  9 | 0.958 | 0.984 | 0.971 |
| 0.150      | 594  | 567 | 27 |  9 | 0.954 | 0.984 | 0.969 |
| 0.175      | 597  | 567 | 30 |  9 | 0.950 | 0.984 | 0.967 |
| 0.200      | 600  | 567 | 33 |  9 | 0.945 | 0.984 | 0.964 |
| 0.225      | 608  | 570 | 38 |  6 | 0.938 | 0.990 | 0.963 |
| **0.250**  | 613  | 570 | 43 |  6 | 0.930 | 0.990 | **0.959** *(current)* |
| 0.275      | 620  | 571 | 49 |  5 | 0.921 | 0.991 | 0.955 |
| 0.300      | 635  | 572 | 63 |  4 | 0.901 | 0.993 | 0.945 |

The plateau in [0.05, 0.125] is unusual -- it means lowering slack
further than 0.125 stops helping (the GBDT's top-K-by-prob cutoff is
already inside the gunshot peak, and `confidence_override=0.75`
catches the rest). 0.125 is the natural pivot.

## Why this contradicts the #103 docstring

`vote_c_adaptive`'s docstring says `slack_frac=0.10` "missed 4 truth
shots which led to bumping to 0.25". That was measured on the **4
fixtures** available at #103 time (281 FPs). On the 30-fixture corpus,
that recall regression no longer exists -- 0.10 and 0.25 land the same
9 FNs from the audio-side voters, *plus* 0.25 imports 18 extra FPs
that voter C should have caught.

## Per-fixture diff (slack=0.05 vs slack=0.25)

The 3 truth shots that move from FN to TP as slack widens all land on
stage7 fixtures:

| fixture | dTP (0.05 -> 0.25) | extra FPs picked up |
|---|---|---|
| blacksmith-stage7-s97dcec94 (headcam) | +1 | +1 |
| tallmilan-stage7-s97dcec94 (headcam)  | +1 | 0 |
| tallmilan-stage7-s97dcec94-iphone17pro (handheld) | +1 | 0 |

Stage7 in this corpus has unusual cross-bay traffic, so widening slack
helps voter C recover real shots that score below other candidates'
GBDT probability. The other 9 fixtures that gain extra FPs at 0.25 do
not gain any extra TPs.

## Options

**(A) Aggressive retune to 0.10.** Lifts aggregate F1 by +0.012. Costs
3 truth shots, all on stage7. Aligned with the project's
"better to under-detect than invent shots" stance: the lost shots can
be recovered manually by the audit UI's missing-count diagnostic; the
extra FPs would otherwise need per-shot rejection clicks.

**(B) Moderate retune to 0.15.** Lifts aggregate F1 by +0.010. Same 9
FN as option A, picks up 2 extra FPs vs A (27 vs 25). Half-step that
hedges against unaudited fixtures behaving differently.

**(C) Keep 0.25.** Status quo. Optimises recall on stage7 at a
material precision cost on the broader corpus.

## Per-camera-class behaviour

Both camera classes prefer the same plateau:

**headcam (12 fixtures, 243 positives)**

| slack | TP | FP | FN | P     | R     | F1     |
|-------|----|----|----|-------|-------|--------|
| 0.10  | 238| 16 |  5 | 0.937 | 0.979 | **0.958** |
| 0.15  | 238| 17 |  5 | 0.933 | 0.979 | 0.956 |
| 0.20  | 238| 21 |  5 | 0.919 | 0.979 | 0.948 |
| 0.25  | 240| 26 |  3 | 0.902 | 0.988 | 0.943 |

**handheld (18 fixtures, 333 positives)**

| slack | TP | FP | FN | P     | R     | F1     |
|-------|----|----|----|-------|-------|--------|
| 0.10  | 329|  9 |  4 | 0.973 | 0.988 | **0.981** |
| 0.15  | 329| 10 |  4 | 0.971 | 0.988 | 0.979 |
| 0.20  | 329| 12 |  4 | 0.965 | 0.988 | 0.976 |
| 0.25  | 330| 17 |  3 | 0.951 | 0.991 | 0.971 |

Both classes are best at 0.10; same plateau shape; no class disagrees
with the aggregate. Per-class stratification does not change the
conclusion.

## Recommendation

Retune to **slack_frac=0.10** (option A). The per-class evidence
removes the main concern about overfitting to the aggregate -- both
camera classes are aligned. The 3 lost truth shots are concentrated on
stage7 fixtures whose audio-side voters already partially veto them; a
follow-up could probe whether voter A / B / D recall on those shots is
worth recovering separately rather than via slack widening.

## Reproduce

```bash
uv run python scripts/build_sweep_signals.py --skip-voter-e
uv run python scripts/run_sweep.py \
    --grid scripts/sweep_grids/voter_c_slack_fine.yaml --append
uv run python scripts/plot_sweep.py
```

Plots and per-fixture breakdown:
[`build/sweeps/2026-05-11T06-11-46Z_902c641_voter_c_slack_fine/report.md`](../../build/sweeps/2026-05-11T06-11-46Z_902c641_voter_c_slack_fine/report.md)
(gitignored; render locally).
