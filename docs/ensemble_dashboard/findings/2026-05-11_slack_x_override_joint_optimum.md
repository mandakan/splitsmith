# Finding: voter C's two knobs are independent; joint optimum is marginal

**Date:** 2026-05-11
**Sweep run_id:** `2026-05-11T07-42-53Z_6e78de4_slack_x_override`

## TL;DR

The 2D sweep of `voter_c_slack_frac` (6 values, 0.05..0.20) and
`voter_c_confidence_override` (6 values, 0.50..0.75) confirms the two
knobs act on **disjoint candidate populations** and have **no
interaction** in the production-relevant range. The joint optimum is
`slack=0.10, override=0.55` (F1=0.9744), one tiny step from the
post-#290 production `slack=0.10, override=0.60` (F1=0.9735). The
step trades 1 extra headcam FP for 2 extra TPs (1 handheld, 1
headcam) and is **not strictly Pareto-clean** -- I'm flagging it as
a candidate for the next corpus expansion rather than shipping it
now.

## F1 heatmap

|             | ovr=0.50 | ovr=0.55 | ovr=0.60 | ovr=0.65 | ovr=0.70 | ovr=0.75 |
|-------------|----------|----------|----------|----------|----------|----------|
| slack=0.050 | 0.9744   | 0.9744   | 0.9735   | 0.9735   | 0.9718   | 0.9709   |
| slack=0.075 | 0.9744   | 0.9744   | 0.9735   | 0.9735   | 0.9718   | 0.9709   |
| **slack=0.100** | 0.9744 | **0.9744** | **0.9735** *(prod)* | 0.9735 | 0.9718 | 0.9709 |
| slack=0.125 | 0.9744   | 0.9744   | 0.9735   | 0.9735   | 0.9718   | 0.9709   |
| slack=0.150 | 0.9728   | 0.9728   | 0.9719   | 0.9719   | 0.9701   | 0.9692   |
| slack=0.200 | 0.9679   | 0.9679   | 0.9669   | 0.9669   | 0.9652   | 0.9643   |

## Independence: the FP and FN columns separate

* **FN column is constant across slack rows 0.05..0.20**: it depends
  only on override. Going 0.75 -> 0.50 takes FN 9 -> 4 at every slack.
* **FP column is mostly slack-controlled**: identical at slack
  0.05..0.125, then +2 at slack=0.15, +9 at slack=0.20.

The two knobs operate on disjoint candidate populations and so their
effects are additive. This is the structural reason the 1D sweeps
in #286 and #290 generalised cleanly to the joint case.

## Why 0.55 is candidate-of-record (not 0.50)

| override | corpus FPs | corpus FNs | per-class FP cost |
|----------|------------|------------|--------------------|
| 0.50     | 26         | 4          | +1 headcam FP vs 0.55 (no extra TP) |
| **0.55** | 26         | 4          | identical metrics, more buffer |
| 0.60 *(prod)* | 25  | 6          | -- |

0.50 and 0.55 are byte-identical on the corpus but 0.55 sits further
from any candidate score, so a future fixture landing a candidate
near 0.52 wouldn't flip the metric. 0.55 is the conservative pick
between equal-metric values.

## Why I'm not shipping a 0.60 -> 0.55 retune this round

* **The trade is not Pareto.** Per-class, 0.60 -> 0.55 costs 1
  headcam FP and rescues 1 headcam TP (1-for-1) plus 1 handheld TP
  for free. Aggregate: +1 FP, +2 TPs. The earlier 0.75 -> 0.60
  retune (#290) was strictly Pareto and that was its strength.
* **F1 lift is tiny** (+0.0009).
* **Shipping cadence**: we already landed #286 and #290 on the same
  voter-C surface. A third retune that close to the previous one
  invites overfitting concerns when the next audited fixtures land.
  Better to wait for corpus growth and re-sweep then.

## The leverage map after six sweeps

After six sweeps the dashboard has characterised every audio-side
parameter:

| knob | leverage | shipped |
|---|---|---|
| `voter_c_slack_frac` | High | 0.25 -> 0.10 (#286) |
| `voter_c_confidence_override` | Real | 0.75 -> 0.60 (#290), 0.55 deferred (this doc) |
| `voter_c_slack_frac × override` | None (independent) | -- |
| `consensus` | None | -- |
| `apriori_boost` | None | -- |
| `voter_a/b_threshold_offset` | None | -- |
| `voter_d_threshold_offset` | None | -- |
| Voter E | Untested -- needs CLIP rebuild | -- |

**Audio-side voter tuning is exhausted on the current corpus.** Further
F1 gains have to come from somewhere else: Voter E (rebuild needed),
audit-set expansion (sharpens calibrated thresholds), or attacking the
candidate generator (issue #7 -- all 9 audio-side FNs are rank-cut,
not detector-miss).

## Reproduce

```bash
uv run python scripts/run_sweep.py --grid scripts/sweep_grids/slack_x_override.yaml --append
uv run python scripts/plot_sweep.py --run-id <run_id>
```
