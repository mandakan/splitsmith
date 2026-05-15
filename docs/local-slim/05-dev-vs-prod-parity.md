# 05 -- Dev vs prod parity

This doc defines how the codebase supports two inference backends
(ONNX in prod, torch in dev) without drift, and what parity guarantee
the slim plan ships with.

The constraint from doc 00: production must never silently disagree
with the calibrated torch reference. A delta the parity test catches
is a release blocker; a delta the parity test misses is a bug in the
test.

## The two backends

| Backend          | Used by                                  | Models live where | Imports |
| ---------------- | ---------------------------------------- | ----------------- | ------- |
| **ONNX Runtime** | All end users with the slim wheel install. The default everywhere onnxruntime is installed. | `~/.splitsmith/models/artifacts/<sha>/...` (doc 03) | `onnxruntime`, `numpy` |
| **Torch**        | Contributors with the dev extras installed who want to debug a model against the original PyTorch checkpoints. | `~/.cache/huggingface/hub/` and `~/panns_data/` | `torch`, `transformers`, `panns_inference` |

The two backends are equally first-class for **inference**. The
parity test is bidirectional: changes to either backend must keep
both producing the same numeric output within tolerance.

The two backends are **not** equally first-class for **training**.
Training and calibration use torch exclusively. ONNX is a forward-
inference target, not a training format.

## Runtime backend selection

A new module `src/splitsmith/ensemble/backend.py` owns the
selection. It is the only place in the codebase that imports
`onnxruntime` and `torch` -- every voter calls into it through
typed wrappers.

```python
# src/splitsmith/ensemble/backend.py
class Backend(str, Enum):
    ONNX = "onnx"
    TORCH = "torch"

def select_backend(override: Backend | None = None) -> Backend:
    """Pick the backend for this process.

    Order of precedence:
      1. Explicit override (env var SPLITSMITH_BACKEND or argument).
      2. ONNX if onnxruntime is importable.
      3. Torch if torch is importable.
      4. Raise SplitsmithBackendError with both install hints.
    """
```

Every voter's runtime-loading function (`load_clap_runtime`,
`load_pann_runtime`, `load_visual_runtime`) returns a single dataclass
that hides the backend-specific session / module object behind a
typed callable. The voter inference functions
(`compute_clap_similarities`, etc.) call the typed callable and
don't know which backend they're using.

```python
@dataclass
class ClapRuntime:
    encode_audio: Callable[[np.ndarray], np.ndarray]  # (N, T) -> (N, 512)
    text_embeddings: np.ndarray                       # (P, 512), precomputed
    backend: Backend
```

The ONNX implementation wraps an `onnxruntime.InferenceSession`. The
torch implementation wraps the `transformers.ClapModel` call. Both
return numpy arrays. The voter never sees a torch tensor.

This shape has three useful properties:

- **The hot path imports either onnxruntime or torch, never both.**
  Cold-import cost is paid once.
- **Voter code is backend-agnostic.** No `if backend == ...`
  branches in the detection pipeline. Easier to reason about.
- **Mocking is trivial.** A test that wants a deterministic
  embedding can construct a `ClapRuntime` with a hand-written
  callable; no ONNX or torch required.

## Environment override

`SPLITSMITH_BACKEND=torch` forces the dev backend even if
`onnxruntime` is also installed. Useful for debugging when an ONNX
parity failure shows up in CI and a contributor wants to confirm the
torch path locally.

The CLI also accepts `--backend torch|onnx` on commands that touch
detection. Default behaviour: respect the env var, otherwise auto.

## Parity test contract

The parity suite lives in `tests/test_onnx_parity.py` and runs only
when both backends are importable (skipped on slim-only CI jobs;
required on the dev-extra CI job).

### Fixture inputs

Each test loads one of the existing audited fixtures from
`tests/fixtures/` (the same fixtures the build script trains on).
The fixture audio is fed through:

- The torch backend's full path (load model, encode audio).
- The ONNX backend's full path (load session, encode audio).

Both paths share the **same NumPy pre-processing** (resample,
window slicing, mel spectrogram, image normalisation). The only
divergence is the model forward pass.

### Per-voter tolerances

| Voter / output                                      | Metric                | Tolerance |
| --------------------------------------------------- | --------------------- | --------: |
| `compute_clap_similarities`                         | L_inf on similarity matrix `(N, P)` | `1e-4` |
| `clap_diff_from_similarities` (Voter B signal)      | L_inf on `(N,)` vector | `1e-4`  |
| `compute_pann_gunshot_probs` (Voter C feature)      | L_inf on `(N,)` vector | `1e-4`  |
| `compute_clip_visual_embeddings` (Voter E feature)  | L_inf on embedding matrix `(N, 512)` (after L2 normalisation) | `5e-4` |
| `voter_c_predict_proba` (full GBDT P(shot))         | L_inf on `(N,)` vector | `5e-4`  |
| Final consensus shot times                          | Per-stage list equality | exact (no time delta) |

The looser CLIP tolerance reflects the fact that the visual tower
has more operator kinds (LayerNorm, scaled dot-product attention)
where ONNX export introduces small floating-point reorderings.
Empirically `5e-4` is loose enough for export drift but tight enough
to catch genuine bugs.

The "exact" tolerance on final consensus shot times is the most
important gate: it means **whatever numeric drift exists upstream
must not move a shot across the consensus threshold**. If a parity
test fails here, the export is either buggy or needs recalibration.

### Asymmetric runs

The parity suite also runs the **whole ensemble** end-to-end on each
fixture and asserts that the count of detected shots and their
timestamps match across backends to within the per-stage tolerance
from `CLAUDE.md` (±15 ms). This catches drift the per-voter tests
miss when the drift accumulates across voters.

### CI gates

The parity suite runs:

1. On every PR touching `src/splitsmith/ensemble/**`,
   `src/splitsmith/data/**`, or `scripts/build_ensemble_artifacts.py`.
2. After every model export in
   `scripts/build_ensemble_artifacts.py --onnx`. The export script
   itself fails fast if parity is violated, before any artifact is
   written to R2.
3. As part of the slim release workflow. The release pipeline
   refuses to publish a wheel whose bundled calibration references
   artifact SHAs that have not been parity-tested in CI.

## Cold-start parity

A separate, much smaller test ensures the slim runtime doesn't
accidentally import torch. `tests/test_no_torch_in_prod_path.py`
patches `sys.modules` so that `torch` is `None`, then imports
`splitsmith.ensemble` and runs a synthetic detection. If any module
on the import path tries `import torch` unconditionally, the test
explodes with a clear traceback pointing at the offender.

This is cheap and worth running on every PR -- it would catch a
"someone added `import torch` at module top-level" regression
immediately.

## How dev contributors switch backends

Two cases:

### Iterating on a model architecture

Contributors editing CLAP / CLIP / PANN handling want torch in the
loop. They install the dev extras (`uv sync --all-groups`) and run
their detection commands with `SPLITSMITH_BACKEND=torch` or
`--backend torch`. The export script (`build_ensemble_artifacts.py
--onnx`) re-exports ONNX when they are ready to compare.

### Debugging a parity failure

If the parity test fails, the contributor:

1. Re-runs locally to confirm the failure direction (which backend
   is the deviant one).
2. Bisects to the offending operation. The parity test reports
   per-voter deltas so it's usually obvious whether the issue is
   pre-processing (NumPy mel doesn't match `ClapProcessor`'s mel)
   or model forward (export op replacement caused drift).
3. Either fixes the export (e.g. switches an operator) or tightens
   the NumPy pre-processing to match the torch reference exactly.

## Numeric drift the plan accepts

Some drift is irreducible:

- ONNX Runtime's CPU provider uses different math libraries than
  PyTorch's CPU backend. Element-wise differences at the `1e-6`
  level are normal.
- Apple Silicon's CoreML provider, if enabled, can drift further
  (`1e-3` is not unusual on some operators). v1 does **not** enable
  CoreML; the default CPU provider is used everywhere.
- INT8 quantisation, if pursued in v2, will require revisiting these
  tolerances.

The parity tolerances above are calibrated against FP32 CPU
inference on both backends, which is what v1 ships.

## What this doc deliberately does not cover

- **Performance parity.** It is expected that ONNX is faster than
  torch on CPU (it almost always is). The plan doesn't promise a
  speedup, just numerical correctness. Cold-start improvements are
  documented in doc 01.
- **Cross-platform parity.** Linux Intel vs macOS arm64 vs Linux
  arm64 may show small per-operator deltas. The parity tests run on
  one platform per PR (whatever the CI matrix picks); a release
  smoke test on at least one other platform catches gross
  regressions.
- **Training-side parity.** Training stays torch-only. ONNX is an
  inference target.

## Open questions

- **CoreML execution provider on macOS.** Apple Silicon users would
  see a measurable speedup with CoreML enabled. The numeric drift
  question above means we hold off in v1, but if measurements show
  the drift stays inside the parity budget for our specific models,
  v2 could enable it by default.
- **Whether to ship a `--strict-parity` mode.** A debug mode where
  every detection call also runs the torch path and compares,
  raising on drift. Useful for paranoid CI; not a v1 feature
  because it requires both backends installed in prod.
- **How to version the parity tolerances.** The numbers above are
  initial picks. If empirical drift on the first export is tighter
  than the budget, we may tighten the budget to make future
  regressions louder. Adjusting later is a calibration-file edit.
