# Voter C: stage-relative features

Date: 2026-10-09. Status: approved.

## Why

Voter C judges each candidate on absolute level and timbre. That breaks when a camera's audio changes
under us, and nothing tells the user. Two cases, both recorded in `docs/cameras.md`:

- **Meta Vanguard firmware.** April 2026 footage clipped every shot; September 2026 footage is clean
  with a gated tail. Voter C trained without the September match scored none of its 180 shots above
  the headcam threshold (recall 0.017 in the spike below, 0.000 in the build's held-out report).
  The app only kept working because the per-stage top-K mode used the round count.
- **GO 3S treble loss.** Each successive shot in a string is duller; held-out recall falls from 0.99
  on the first shot of a string to about 0.69 from the sixth on.

A shot's relation to the stage's other shots survives both: a September Vanguard shot is still the
loudest, sharpest thing in its stage, whatever its absolute numbers.

## Evidence (spike, 2026-10-09)

Leave-one-match-out on the 37 headcam and 105 handheld fixtures, shipped GBDT settings, each held-out
match thresholded at 95 % recall from 5-fold CV inside the training matches only (throwaway code; the
numbers are reproducible from `build/sweeps/signals.parquet`).

| Variant | Headcam R / P / F1 | Höstfinalen Vanguard recall | GO 3S shot 6+ recall | Handheld F1 |
|---|---|---|---|---|
| Today's features | 0.739 / 0.811 / 0.774 | 0.017 | 0.69 | 0.962 |
| + spectral centroid, high band | 0.776 / 0.795 / 0.786 | 0.183 | 0.67 | 0.966 |
| **+ stage-relative** | **0.959 / 0.821 / 0.885** | **0.917** | 0.75 | 0.966 |
| + stage-relative, round count ignored | 0.949 / 0.826 / 0.883 | 0.917 | 0.72 | not run |
| + running-relative (vs previous shots) | 0.795 / 0.814 / 0.804 | 0.272 | 0.61 | 0.967 |

Cost: Blacksmith 2026 GO 3S held-out precision 0.918 -> 0.805 (0.831 without the round count).
Running-relative adds nothing on top of stage-relative and is left out.

## Decisions

- Add **two absolute spectral columns** and a **stage-relative block**. No running-relative block.
- The reference ("likely shots") is chosen **without labels**, the same way in the trainer and at
  runtime.
- **One implementation** in `ensemble/features.py`, called by both `detect_shots_ensemble` and
  `scripts/build_ensemble_artifacts.py`, as `beep_features` already is for the beep ranker.
- Headcam and handheld both get the new columns (handheld is neutral to slightly better; one layout
  keeps the code simple).
- **Accepted trade (user, 2026-10-09):** GO 3S precision on Blacksmith 2026, held out, drops from
  0.918 to about 0.81 to 0.83, in exchange for surviving camera audio changes. Recorded in the PR and
  in `docs/cameras.md` under the GO 3S, so it can be revisited.

## Reverting

Code and artifacts change together (the ONNX input width ties them), so the revert is a `git revert`
of the PR: it restores the old feature code and the old shipped artifacts in one step, and the width
guard catches any mix. No config switch keeps both feature paths alive; that would mean shipping and
maintaining two artifact sets. If the trade needs revisiting without a full revert, the cheaper levers
are a per-camera-class choice of layout (headcam only) or dropping the relative columns that cost the
GO 3S precision, each a retrain.

## Design

### New columns

Absolute, per candidate, from the 40 ms starting 2 ms after the onset (Hann window, 4096-point FFT,
200 Hz to 16 kHz band):

- `spectral_centroid_hz`
- `high_band_db`: energy above 4 kHz relative to the band, in dB

Stage-relative, per candidate, for each of these source columns:
`peak_amp, rms_post, tail_amp, peak_floor_ratio, spectral_flatness, spectral_peak_ratio, rms_ratio`
(log scale: `log(x) - median(log(ref))`) and
`attack, ratio_1_20, ratio_5_20, gunshot_prob, clap_diff, spectral_centroid_hz, high_band_db,
clap_sim_00..09` (linear: `x - median(ref)`). That is 7 + 17 = 24 columns, named `rel_<source>`.
`attack` is signed (negative when the 10 ms before the onset holds a louder sample, 42 % of
candidates), so it is linear; the first draft logged it, which clamped every negative value to one
number (found in the final review).

### The reference set

Candidates of the same stage (one detector universe, the one voter C already scores), ranked by
detector confidence (stable sort). The reference is the top `K = max(3, round(0.3 * N))`, clamped
to `N`, **whether or not the round count is known**.

Amended 2026-10-09 (user-approved): the first version used `K = expected_rounds` when known. Nearly
every training fixture has a round count, so the model learned the features with that reference,
and a stage scored without one got a differently defined reference: in-sample, without round
counts, headcam false positives rose from 26 to 44. One rule everywhere removes the mismatch; the
spike measured no cost for it (headcam held-out F1 0.883 against 0.885). The round count keeps its
existing job in voter C's top-K mode.

`N = 0` returns an empty block. A NaN source value (a candidate too close to the clip end for the
spectral window) is excluded from the median and its own relative value is 0.

### Layout and versioning

`voter_c_feature_matrix` grows from `hand | clap_sims | clap_diff | gunshot_prob | camera_onehot` to
`hand (+2 spectral) | clap_sims | clap_diff | gunshot_prob | camera_onehot | rel (24)`.
`VOTER_C_FEATURE_DIM` moves with it. The calibration JSON already records `voter_c_feature_dim` and the
ONNX session refuses a mismatched width; the runtime must turn that into a clear error naming the
artifact and the expected width, so an old artifact set under `SPLITSMITH_ARTIFACTS_DIR` fails loudly
instead of scoring garbage. Old artifact sets cannot be A/B'd against new code after this change;
compare by checking out the old commit.

### Trainer

`build_ensemble_artifacts.py` already builds rows per fixture (one stage), so it computes the block per
fixture with `expected_rounds` from `stage_rounds.expected`, through the shared function. The held-out
report gains nothing new: its leave-one-match-out split is the acceptance test.

### Runtime

`detect_shots_ensemble` computes the block after the hand, CLAP and PANN features, passing the
`expected_rounds` it already receives. No new I/O; cost is a sort and a few medians per stage plus one
FFT per candidate for the spectral columns (the hand features already take one per candidate).

## Acceptance

Measured by the build's own held-out report and by the engine over every fixture, old artifacts vs new
(the per-fixture comparison used for the 2026-10-09 retrain):

- Headcam leave-one-match-out F1 at least 0.86 (today 0.778), Höstfinalen Vanguard recall at least 0.85.
- Handheld leave-one-match-out F1 not below today's by more than 0.005.
- In-app mode (with round counts), per-fixture comparison: every newly worse fixture listed in the PR,
  and headcam errors (FP + FN) not above the post-#1355 artifacts.
- `docs/cameras.md` updated with what changed for each camera, including the GO 3S precision trade.

## Testing

- **Gain invariance**: scaling a fixture's audio by -12 dB leaves every `rel_` level column unchanged
  within tolerance (fails against today's code, which has no such columns, and against a version that
  forgets the log).
- **Timbre invariance**: low-passing a whole fixture moves `spectral_centroid_hz` but leaves
  `rel_spectral_centroid_hz` of the shots near 0.
- **Reference choice**: with `expected_rounds`, the reference is exactly the top-K by confidence; without
  it, `max(3, round(0.3 * N))`; `N = 0` and `N = 1` do not raise.
- **One implementation**: the trainer and runtime produce identical matrices for a fixture (extends
  `test_onnx_parity.py`'s reference).
- **Width guard**: an artifact with the old width raises the named error.

## Order

#1355 (the retrain with the Höstfinalen fixtures) lands first: it fixes the September Vanguard footage
now. This change retrains again on top of it.

## Risks

- Four headcam matches and one observed firmware change: strong evidence, not proof. The held-out
  report is re-read on every retrain from now on.
- A stage where most candidates are noise (very few shots, much movement) gives a poor reference; the
  top-K by confidence and the round count limit that, and the spike's no-round-count run held up.
- Precision on an unseen match stays around 0.82 for voter C alone; consensus and the round-count mode
  sit on top of it in the app.
