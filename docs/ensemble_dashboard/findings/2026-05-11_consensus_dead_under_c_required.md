# Finding: consensus is a dead axis under c_required=True

**Date:** 2026-05-11
**Sweep run_id:** `2026-05-11T07-31-38Z_33a361a_consensus_x_c_required`
**Corpus:** 30 audited fixtures, 1713 candidates, 576 positives

## TL;DR

Sweeping consensus (1..5) under `c_required=True` (production) moves
F1 by **0.0008** across consensus 1..4 then collapses at 5. Under
`c_required=False`, the same sweep moves F1 by **0.4081** -- from
0.50 to 0.91 -- but never reaches the F1 we already have with
`c_required=True`.

This confirms the inverse of the A/B/D leverage finding: in the
production configuration **voter C's veto (`c_required=True`) does
essentially all the precision work**, and the consensus threshold +
A/B/D thresholds together contribute almost nothing.

## Numbers

| consensus | c_required | kept | TP  | FP   | FN  | precision | recall | F1     |
|-----------|------------|------|-----|------|-----|-----------|--------|--------|
| 1         | True       | 592  | 567 | 25   | 9   | 0.958     | 0.984  | 0.9709 |
| 2         | True       | 592  | 567 | 25   | 9   | 0.958     | 0.984  | 0.9709 |
| 3         | True       | 592  | 567 | 25   | 9   | 0.958     | 0.984  | 0.9709 |
| **4**     | True       | 591  | 567 | 24   | 9   | 0.959     | 0.984  | **0.9717** |
| 5         | True       | 385  | 380 | 5    | 196 | 0.987     | 0.660  | 0.7908 |
| 1         | False      | 1712 | 576 | 1136 | 0   | 0.336     | 1.000  | 0.5035 |
| 2         | False      | 1675 | 576 | 1099 | 0   | 0.344     | 1.000  | 0.5118 |
| 3         | False      | 1500 | 576 | 924  | 0   | 0.384     | 1.000  | 0.5549 |
| 4         | False      | 668  | 567 | 101  | 9   | 0.849     | 0.984  | 0.9116 |
| 5         | False      | 385  | 380 | 5    | 196 | 0.987     | 0.660  | 0.7908 |

## Three implications

1. **`c_required=True` collapses the consensus axis.** Below 5,
   consensus 1/2/3 are byte-identical (25 FP, 9 FN); 4 saves exactly
   one extra FP on a handheld fixture (per-class breakdown). The
   production default consensus=3 could be 1 or 2 with no metric
   change.

2. **The C-veto is not interchangeable with a higher consensus
   threshold.** `c_required=False, consensus=4` reaches F1=0.912 --
   way better than the low-consensus rows but still 6 points below
   the c_required=True floor. The C-veto's signal (top-K-by-GBDT
   prob) is fundamentally different from "lots of voters agreed."

3. **There's a free precision win at `consensus=4 + c_required=True`.**
   One extra FP filtered (24 vs 25), 0 recall cost. F1 lift is
   0.0008 -- tiny, single-data-point, almost certainly noise on the
   labeling tolerance. **Not recommending a default change** for one
   FP. Worth re-checking after the next corpus expansion.

## Why `c_required=False, consensus=5` matches the c_required=True case

Both rows show TP=380, FP=5, FN=196, F1=0.7908 -- looks like the
ensemble flips into the same regime. Explanation: under consensus=5
the candidate must clear `vote_total + apriori_boost >= 5`, which on
4 voters means all 4 voted yes AND it's in the top-K-by-confidence
apriori list. That's a stricter condition than the C-veto alone, but
the labeled-positive subset is small enough that the two filters
converge to the same survivors.

## Should we drop `c_required` and just hard-set consensus=4?

No. The per-class evidence shows handheld and headcam disagreeing on
where consensus=4 alone (c_required=False) lands -- handheld
F1=0.956, headcam F1=0.856. The C-veto smooths over that variance.
Keeping `c_required=True` keeps the ensemble robust to per-class
score-distribution shifts.

## What this leaves on the meta-finding map

After three sweeps (`voter_c_slack_fine`, `voter_a/b/d_threshold_offset`,
`consensus_x_c_required`) the leverage map is:

| knob | leverage under c_required=True |
|---|---|
| `voter_c_slack_frac` | **High** -- F1 spread 0.026, retuned 0.25 -> 0.10 |
| `consensus` | None (1..4 identical, 5 collapses) |
| `voter_a_floor` | None |
| `voter_b_threshold` | None |
| `voter_d_threshold` | None (1 FP across +0..+0.10) |
| `voter_c_confidence_override` | Untested -- next target |
| `apriori_boost` | Untested -- next target |
| Voter E | Untested -- needs CLIP rebuild |

## Reproduce

```bash
uv run python scripts/run_sweep.py --grid scripts/sweep_grids/consensus_x_c_required.yaml --append
uv run python scripts/plot_sweep.py --run-id <run_id>
```
