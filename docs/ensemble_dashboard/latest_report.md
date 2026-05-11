# Ensemble sweep report -- `2026-05-11T06-18-27Z_902c641_voter_c_slack_fine`

![overview](latest_overview.png)

## Summary

- Swept parameters: `voter_c_slack_frac`
- Combos evaluated: 11
- Fixtures in corpus: 30

### Best aggregate F1

- F1 = **0.9709**, precision = 0.9578, recall = 0.9844
- True positives: 567 / 576; false positives: 25; false negatives: 9

Parameters at the best point:

| key | value |
|---|---|
| `apriori_boost` | `1.0` |
| `c_required` | `True` |
| `camera_class_filter` | `all` |
| `consensus` | `3` |
| `e_audio_strong_min_votes` | `4` |
| `e_required` | `False` |
| `enable_voter_e` | `False` |
| `use_expected_rounds` | `True` |
| `voter_a_floor` | `None` |
| `voter_a_floor_offset` | `0.0` |
| `voter_b_threshold` | `None` |
| `voter_b_threshold_offset` | `0.0` |
| `voter_c_confidence_override` | `0.75` |
| `voter_c_mode` | `adaptive` |
| `voter_c_slack_frac` | `0.05` |
| `voter_c_slack_min` | `3` |
| `voter_c_threshold` | `None` |
| `voter_c_threshold_offset` | `0.0` |
| `voter_d_threshold` | `None` |
| `voter_d_threshold_offset` | `0.0` |
| `voter_e_threshold` | `None` |
| `voter_e_threshold_offset` | `0.0` |

### Per camera class (at best aggregate F1)

| class | kept | TP | FP | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|---|
| headcam | 254 | 238 | 16 | 5 | 0.937 | 0.979 | 0.958 |
| handheld | 338 | 329 | 9 | 4 | 0.973 | 0.988 | 0.981 |

## Plots

### precision

![precision](precision_vs_voter_c_slack_frac.png)

### recall

![recall](recall_vs_voter_c_slack_frac.png)

### f1

![f1](f1_vs_voter_c_slack_frac.png)

### per_fixture_f1

![per_fixture_f1](per_fixture_f1_vs_voter_c_slack_frac.png)

### Per-fixture

![per-fixture](per_fixture_bars.png)

## Per-fixture table (at best aggregate F1)

| fixture | camera | kept | TP | FP | FN | P | R | F1 |
|---|---|---|---|---|---|---|---|---|
| stage-shots-tallmilan-2026-stage3-s97dcec94 | headcam | 15 | 15 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-blacksmith-2026-stage5-s97dcec94 | headcam | 24 | 24 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-blacksmith-2026-stage8-s97dcec94 | headcam | 27 | 27 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-tallmilan-2026-stage5-s97dcec94 | headcam | 15 | 15 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-tallmilan-2026-stage5-s97dcec94-apple-iphone17pro | handheld | 15 | 15 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-tallmilan-2026-stage6-s97dcec94-apple-iphone17pro | handheld | 15 | 15 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-blacksmith-2026-stage1-s97dcec94-apple-iphone17pro | handheld | 38 | 38 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-blacksmith-2026-stage5-s97dcec94-apple-iphone17pro | handheld | 24 | 24 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-blacksmith-2026-stage7-s97dcec94-apple-iphone17pro | handheld | 24 | 24 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-blacksmith-2026-stage8-s97dcec94-apple-iphone17pro | handheld | 27 | 27 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-tallmilan-2026-stage2-s36ed6e4e | handheld | 18 | 18 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-tallmilan-2026-stage4-s36ed6e4e | handheld | 19 | 19 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-tallmilan-2026-stage5-s36ed6e4e | handheld | 15 | 15 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-tallmilan-2026-stage6-s36ed6e4e | handheld | 13 | 13 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-tallmilan-2026-stage7-s36ed6e4e | handheld | 19 | 19 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| stage-shots-blacksmith-2026-stage1-s97dcec94 | headcam | 37 | 37 | 0 | 1 | 1.000 | 0.974 | 0.987 |
| stage-shots-tallmilan-2026-stage7-s97dcec94-apple-iphone17pro | handheld | 19 | 19 | 0 | 1 | 1.000 | 0.950 | 0.974 |
| stage-shots-tallmilan-2026-stage6-s97dcec94 | headcam | 14 | 14 | 0 | 1 | 1.000 | 0.933 | 0.966 |
| stage-shots-tallmilan-2026-stage3-s36ed6e4e | handheld | 15 | 14 | 1 | 0 | 0.933 | 1.000 | 0.966 |
| stage-shots-blacksmith-2026-stage7-s97dcec94 | headcam | 23 | 22 | 1 | 1 | 0.957 | 0.957 | 0.957 |
| stage-shots-tallmilan-2026-stage1-s36ed6e4e | handheld | 33 | 33 | 0 | 3 | 1.000 | 0.917 | 0.957 |
| stage-shots-blacksmith-2026-stage2-s97dcec94 | headcam | 15 | 13 | 2 | 0 | 0.867 | 1.000 | 0.929 |
| stage-shots-blacksmith-2026-stage6-s97dcec94 | headcam | 15 | 13 | 2 | 0 | 0.867 | 1.000 | 0.929 |
| stage-shots-blacksmith-2026-stage2-s97dcec94-apple-iphone17pro | handheld | 15 | 13 | 2 | 0 | 0.867 | 1.000 | 0.929 |
| stage-shots-tallmilan-2026-stage7-s97dcec94 | headcam | 19 | 18 | 1 | 2 | 0.947 | 0.900 | 0.923 |
| stage-shots-tallmilan-2026-stage4-s97dcec94 | headcam | 21 | 18 | 3 | 0 | 0.857 | 1.000 | 0.923 |
| stage-shots-tallmilan-2026-stage2-s97dcec94 | headcam | 15 | 12 | 3 | 0 | 0.800 | 1.000 | 0.889 |
| stage-shots-tallmilan-2026-stage2-s97dcec94-apple-iphone17pro | handheld | 15 | 12 | 3 | 0 | 0.800 | 1.000 | 0.889 |
| stage-shots-blacksmith-2026-stage3-s97dcec94-apple-iphone17pro | handheld | 14 | 11 | 3 | 0 | 0.786 | 1.000 | 0.880 |
| stage-shots-blacksmith-2026-stage3-s97dcec94 | headcam | 14 | 10 | 4 | 0 | 0.714 | 1.000 | 0.833 |
