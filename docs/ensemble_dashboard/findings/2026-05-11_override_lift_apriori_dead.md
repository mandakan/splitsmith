# Finding: voter_c_confidence_override is mis-tuned; apriori_boost is dead

**Date:** 2026-05-11
**Sweep run_ids:**
* `2026-05-11T07-34-17Z_33a361a_voter_c_confidence_override`
* `2026-05-11T07-34-18Z_33a361a_apriori_boost`

## TL;DR

Two more knobs swept from the leverage map after the
`consensus_x_c_required` confirmation:

* **`voter_c_confidence_override`** -- real lift available. Dropping
  from the current **0.75 -> 0.60** rescues **3 truth shots on
  handheld fixtures for free** (no extra FPs anywhere). F1:
  0.9709 -> 0.9735 (+0.0026). Per-class consistent.
* **`apriori_boost`** -- another dead axis. F1 is byte-identical at
  0.9709 across the entire [0.0, 2.0] range, just like consensus and
  A/B/D thresholds.

## voter_c_confidence_override sweep (overall)

| override | TP  | FP | FN | precision | recall | F1     |
|----------|-----|----|----|-----------|--------|--------|
| null *(disabled)* | 548 | 25 | 28 | 0.956 | 0.951 | 0.9539 |
| 0.50     | 572 | 26 |  4 | 0.957 | 0.993 | 0.9744 |
| **0.60** | 570 | 25 |  6 | 0.958 | 0.990 | **0.9735** |
| 0.70     | 568 | 25 |  8 | 0.958 | 0.986 | 0.9718 |
| **0.75** *(current)* | 567 | 25 |  9 | 0.958 | 0.984 | 0.9709 |
| 0.80     | 567 | 25 |  9 | 0.958 | 0.984 | 0.9709 |
| 0.85     | 564 | 25 | 12 | 0.958 | 0.979 | 0.9682 |
| 0.90     | 559 | 25 | 17 | 0.957 | 0.971 | 0.9638 |
| 0.95     | 552 | 25 | 24 | 0.957 | 0.958 | 0.9575 |

## Per-class (handheld vs headcam)

**Handheld:** override sweep matters a lot -- 0.75 -> 0.50 lifts F1
0.9806 -> 0.9867 by recovering 4 truth shots that were being rank-
capped despite scoring in [0.50, 0.75] on the GBDT.

| override | handheld TP | handheld FP | handheld F1 |
|----------|-------------|-------------|-------------|
| 0.50     | 333         |  9          | 0.9867      |
| **0.60** | 332         |  9          | 0.9852      |
| 0.70     | 330         |  9          | 0.9821      |
| **0.75** | 329         |  9          | 0.9806      |

**Headcam:** flat in [0.60, 0.80] -- the rank cap is already accepting
all the high-GBDT-prob real shots; the override at any value in this
range neither helps nor hurts.

| override | headcam TP | headcam FP | headcam F1 |
|----------|------------|------------|------------|
| 0.50     | 239        | 17         | 0.9579     |
| **0.60** | 238        | 16         | 0.9577     |
| **0.75** | 238        | 16         | 0.9577     |

## Why I recommend 0.60 over 0.50

0.50 lifts overall F1 by another +0.0009 over 0.60 -- but it does so
by trading 1 extra FP on headcam for 1 extra recovered TP on
handheld. That's the same lopsided FP-vs-FN UX cost we used to argue
in favor of #286 (rejected markers are still on the audit timeline,
one click to flip; FPs need active attention to catch). 0.60 has
**no FP cost at all** -- strictly Pareto-better than 0.75.

## apriori_boost sweep

| apriori_boost | TP  | FP | FN | F1     |
|---------------|-----|----|----|--------|
| 0.00          | 567 | 25 |  9 | 0.9709 |
| 0.25 .. 2.00  | 567 | 25 |  9 | 0.9709 |

Completely flat. Under `c_required=True`, the boost lifts top-K
candidates above the consensus threshold but voter C already vetoed
everything the consensus would have caught. Same root cause as the
consensus-axis collapse: voter C runs the precision filter.

Could be ripped out entirely (`apriori_boost=0` is identical to
`apriori_boost=2.0` on every metric), but leaving it as a knob
costs nothing and preserves the option for a future ensemble that
weakens `c_required`.

## Updated leverage map (after five sweeps)

| knob | leverage under c_required=True |
|---|---|
| `voter_c_slack_frac` | **High** -- retuned via #286 |
| `voter_c_confidence_override` | **Real** -- retune candidate, see this doc |
| `consensus` | None (1..4 identical) |
| `apriori_boost` | None (0.0..2.0 identical) |
| `voter_a/b_threshold_offset` | None |
| `voter_d_threshold_offset` | None |
| Voter E | Untested -- needs CLIP rebuild |

After five sweeps the map is converging: voter C's two knobs are the
only ones that move metrics in the production config. Voter E is the
last unknown.

## Reproduce

```bash
uv run python scripts/run_sweep.py --grid scripts/sweep_grids/voter_c_confidence_override.yaml --append
uv run python scripts/run_sweep.py --grid scripts/sweep_grids/apriori_boost.yaml --append
uv run python scripts/plot_sweep.py --run-id <run_id>
```
