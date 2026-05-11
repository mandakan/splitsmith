# Finding: voters A, B, D have no leverage under c_required=True

**Date:** 2026-05-11
**Sweep run_ids:**
* `2026-05-11T07-10-32Z_5f36fa7_voter_a_floor_offset`
* `2026-05-11T07-10-33Z_5f36fa7_voter_b_threshold_offset`
* `2026-05-11T07-10-35Z_5f36fa7_voter_d_threshold_offset`

## TL;DR

Sweeping each of voters A, B, D's thresholds across a wide range
(±0.05 around the calibrated value for A; +0 to +0.15 for B; +0 to
+0.10 for D) moves aggregate metrics by **0, 0, and 1 false positive
respectively**. The production ensemble is essentially insensitive to
A/B/D thresholds because voter C's veto (`c_required=True`) absorbs
their work first.

| voter | offset range | F1 spread | FP delta |
|---|---|---|---|
| A | [-0.02, +0.05] | 0.0000 | 0 |
| B | [+0.00, +0.15] | 0.0000 | 0 |
| D | [+0.00, +0.10] | 0.0008 | -1 |

For comparison, the voter C slack sweep on the same corpus moves F1
by 0.026 (0.945 -> 0.971) across the same kind of small parameter
range.

## Why this happens

Three voters are calibrated to the lowest positive (100 % recall by
construction):

* Voter A: floor = min(confidence over labeled positives)
* Voter B: threshold = min(clap_diff over labeled positives)
* Voter D: threshold = min(gunshot_prob over labeled positives)

So at the calibrated value, every truth shot votes yes; the only way
sweeping their threshold can change outcomes is by *dropping* truth
shots (going up) or by accepting candidates that all four other
voters already either accept or reject. Voter C runs in adaptive top-
K mode and gates every keep decision (`c_required=True`), so a
candidate voter A/B/D would have dropped is already dropped by C, and
a candidate they'd have kept stays kept only if C agrees.

The result: A/B/D contribute **redundant recall** votes; they don't
contribute precision.

## Implications

* **Tuning effort goes to voter C, full stop.** This is what we
  found on the slack sweep -- the +0.012 F1 gain came entirely from
  voter C, with A/B/D unchanged.
* **Could we drop A/B/D entirely?** Probably not -- they're the
  redundancy that makes `vote_total >= consensus` meaningful, and
  they catch failure modes where C is wrong about a real shot. But
  their *thresholds* don't need tuning past the lowest-positive
  calibration.
* **The dashboard works correctly.** It surfaces the right lesson:
  flat curves mean "this knob doesn't matter."

## Not tested here

* **`c_required=False`** would re-introduce A/B/D leverage by making
  the consensus a pure majority vote. Worth a sweep on a separate
  branch to confirm the inverse hypothesis (A/B/D sweeps move
  outcomes when C is not vetoing). Skipping for now because
  `c_required=False` is known to be much worse on the 281-FP
  calibration set (#103); the experiment is academic.
* **Voter E** (CLIP visual probe). The shipped `signals.parquet`
  was built with `--skip-voter-e` so its column is all NaN. Run
  `scripts/build_sweep_signals.py` (no flag) and sweep
  `voter_e_threshold_offset` once that column is populated.

## Reproduce

```bash
uv run python scripts/run_sweep.py --grid scripts/sweep_grids/voter_a_floor_offset.yaml --append
uv run python scripts/run_sweep.py --grid scripts/sweep_grids/voter_b_threshold_offset.yaml --append
uv run python scripts/run_sweep.py --grid scripts/sweep_grids/voter_d_threshold_offset.yaml --append
uv run python scripts/plot_sweep.py --run-id <one of the run_ids>
```

Plots in `build/sweeps/<run_id>/` (gitignored; render locally).
