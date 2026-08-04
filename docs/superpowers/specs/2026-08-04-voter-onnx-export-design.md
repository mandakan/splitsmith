# Voter C / Voter E ONNX export (issue #649)

Retire the two shipped scikit-learn pickles in favour of ONNX graphs
loaded through the `onnxruntime` path that CLAP and PANN already use,
then drop scikit-learn from the runtime dependency set.

## Why

`src/splitsmith/data/voter_c_gbdt.joblib` pickles a
`GradientBoostingClassifier`. The unpickle binds the artifact to the
scikit-learn version the *installing user* resolves, not the one in
`uv.lock`. sklearn 1.9 dropped a `sys.modules["_loss"]` side effect the
pickle depended on and every fresh install died with
`ModuleNotFoundError: No module named '_loss'`. #648 pinned
`scikit-learn>=1.8.0,<1.9` as a holding action.

`voter_e_visual_probe.joblib` (a `LogisticRegression`) still loads under
1.9, but sklearn warns the cross-version estimator "might lead to
breaking code or invalid results" -- a quieter failure than voter C's
hard crash.

## Measured feasibility

Exported with `skl2onnx.to_onnx(..., options={id(clf): {"zipmap": False}})`
against the shipped artifacts:

| Artifact | joblib | ONNX | L_inf vs sklearn |
| --- | ---: | ---: | ---: |
| voter C `headcam` | (528 KB for both classes) | 110 KB | 1.3e-07 |
| voter C `handheld` | | 106 KB | 1.9e-07 |
| voter E probe | 5.0 KB | 5.5 KB | 1.6e-07 |

Measured over 2048 standard-normal rows. `docs/local-slim/05` sets the
`voter_c_predict_proba` tolerance at 5e-4, so there is four orders of
magnitude of headroom.

Input tensors must be declared `FloatTensorType`. `DoubleTensorType`
converts but the session fails to instantiate: onnxruntime reports
`Type (tensor(double)) of output arg (probabilities) ... does not match
expected type (tensor(float))` for `TreeEnsembleClassifier`.

## Artifact layout

All three land in `src/splitsmith/data/` next to
`ensemble_calibration.json` and ship in the wheel, exactly as the
joblibs do today. They are small enough that the R2 model registry used
for CLAP/PANN is unnecessary.

```
src/splitsmith/data/voter_c_gbdt_headcam.onnx
src/splitsmith/data/voter_c_gbdt_handheld.onnx
src/splitsmith/data/voter_e_visual_probe.onnx
```

`ensemble_calibration.json` gains one field and repoints another:

```json
"voter_c_onnx_artifacts": {
  "headcam": "voter_c_gbdt_headcam.onnx",
  "handheld": "voter_c_gbdt_handheld.onnx"
},
"voter_e_probe_artifact": "voter_e_visual_probe.onnx"
```

The filename map is explicit rather than derived from the class name so
a new camera class needs no code change, and so a hand-assembled
experimental artifact set (`SPLITSMITH_ARTIFACTS_DIR`) can name files
freely.

## Runtime contract

`OnnxProbaModel` in `src/splitsmith/ensemble/calibration.py` wraps one
`onnxruntime.InferenceSession` and exposes the single method every call
site already uses:

```python
class OnnxProbaModel:
    def __init__(self, path: Path) -> None: ...
    @property
    def n_features(self) -> int: ...
    def predict_proba(self, X: np.ndarray) -> np.ndarray:  # (N, 2) float64
```

- Casts the input to contiguous float32 before the run.
- Raises `ValueError` naming the artifact when `X.shape[1]` does not
  match the graph's declared feature count.
- Reads the output named `probabilities`, falling back to output index 1.
- Returns float64 so downstream threshold comparisons keep the dtype
  they have today.

`load_voter_c_model(artifacts=None)` returns `dict[str, OnnxProbaModel]`
keyed by camera class. `load_voter_e_probe(filename=None)` returns an
`OnnxProbaModel` or `None` when the file is absent. Both keep their
current return shape, so `api.py` and `visual.py` call sites and the
`_StubGBDT` seam in `tests/test_ensemble.py` are untouched.

When `voter_c_onnx_artifacts` is missing from the calibration -- an
artifact set built before this change -- the loader raises a
`RuntimeError` naming `scripts/build_ensemble_artifacts.py`. There is no
joblib fallback: a fallback would reinstate the version coupling this
change exists to remove. This breaks any existing
`SPLITSMITH_ARTIFACTS_DIR` pointing at a joblib-only set, which is
accepted, with the error message carrying the fix.

## Build script

`scripts/build_ensemble_artifacts.py` stops writing `.joblib` and writes
the three `.onnx` files instead, via a `_export_onnx_proba` helper. Both
joblibs are deleted from the repository.

It also freezes a parity reference so the parity test needs neither
scikit-learn nor the pickles:

```
tests/data/voter_c_parity_reference.npz   X (N, 31) float32,
                                          proba_headcam (N,) float64,
                                          proba_handheld (N,) float64,
                                          threshold_headcam, threshold_handheld
tests/data/voter_e_parity_reference.npz   X (M, 512) float32,
                                          proba (M,) float64, threshold
```

`X` is the real calibration feature matrix -- the same matrix
`_train_voter_c_for_class` fits on, not synthetic data -- and the
probabilities are the sklearn estimator's own output captured at build
time. Each voter C class model is scored over the *whole* matrix, not
only its own class's rows, for wider tree coverage. Voter E rows are
deterministically subsampled to at most 1024 to keep the file near 2 MB.
The reference files are only written when the `tests/` directory exists,
so a wheel-context run of `build_artifacts` (the dev retrain endpoint)
does not fail.

## Parity test

`tests/test_onnx_parity.py` gains three always-on tests -- unlike the
CLAP/PANN tests in that file, these need no env vars and no downloads,
so they run on every CI job:

1. `test_voter_c_onnx_matches_frozen_sklearn_reference` -- L_inf between
   the ONNX probabilities and the frozen sklearn probabilities is below
   5e-4, per class.
2. `test_voter_e_onnx_matches_frozen_sklearn_reference` -- same, 5e-4.
3. `test_voter_c_onnx_vote_decisions_match_reference` -- the boolean
   `proba >= threshold` vector is *exactly* equal for both classes. A
   probability nudge only matters if it flips a vote, so this is the
   assertion that maps to user-visible behaviour.

## Dependencies

- `scikit-learn` and `joblib` move from `[project].dependencies` to the
  `dev` group. Nothing under `src/` imports either once the loaders
  change; the only remaining users are `scripts/` and the tests.
  (librosa still pulls scikit-learn in transitively, so the install does
  not shrink -- what goes away is *our* version constraint.)
- `skl2onnx>=1.17` joins the `dev` group next to `onnx` / `onnxscript`,
  described there as export-side tooling.
- `onnxruntime` is already a runtime dependency; no change.

## Affected supporting files

- `scripts/ci/assert_ensemble_artifacts.py` -- assert the ONNX sessions
  instantiate and their declared input dimensions match
  `voter_c_feature_dim`; delete the `InconsistentVersionWarning` gate,
  which no longer has a pickle to guard.
- `scripts/smoke_slim_install.sh` -- the sentinel becomes "the ensemble
  ONNX artifacts must load under the resolved onnxruntime". It cannot
  assert scikit-learn is absent, because librosa depends on it.
- `docs/local-slim/02-onnx-migration.md` -- the "two model artifacts stay
  where they are" section is now wrong; replace it with what shipped.
- `docs/local-slim/06-slim-progress.md`, `CLAUDE.md`, `SPEC.md` -- prose
  references to the joblib artifacts.
- `src/splitsmith/ui_static/src/pages/dev/DevRetrain.tsx:47` -- the step
  label "Save calibration JSON + joblib".

## Out of scope

Voter E's CLIP backbone stays torch-only (`visual.py`'s ONNX branch
still raises `NotImplementedError`). This change covers the linear probe
head, not the encoder in front of it.
