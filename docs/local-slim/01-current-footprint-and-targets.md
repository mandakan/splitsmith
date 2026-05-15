# 01 -- Current footprint and targets

This doc has the numbers behind the slim plan. Doc 00 names the
target (<300 MB total after first detection) at a high level; this
doc breaks the current install down byte by byte and shows exactly
which lines move where.

All measurements were taken on macOS arm64 in a clean Python 3.14
venv built from `pyproject.toml` at commit `a915b1e` (the
"clean-prose docs" commit shipped 2026-05-14). Numbers will vary a
little across Python versions and OSes but the proportions hold.

## Today's install -- baseline measurements

### Wheel install (`uv tool install splitsmith`)

`.venv` total: **~1.1 GB**.

| Wheel / package          | Disk size | Role today | After slim |
| ------------------------ | --------: | ---------- | ---------- |
| `torch`                  |    380 MB | Hosts CLAP, PANN, CLIP inference at runtime | Removed from default deps; lives in `[dev]` extra |
| `scipy`                  |     82 MB | Genuine: `librosa` and envelope detector | Stays |
| `transformers`           |     50 MB | Loads CLAP + CLIP via `from_pretrained` | Removed from default; lives in `[dev]` extra |
| `numpy`                  |     24 MB | Genuine: every numeric module | Stays |
| `librosa`                |    4.3 MB | Audio I/O + onset detection | Stays |
| Everything else combined | ~560 MB | FastAPI, uvicorn, pydantic, joblib, sklearn, soundfile, typer, rich, pyyaml, httpx, mcp, transitive C extensions | Stays as-is |

The "everything else" line is dominated by transitive C extensions
(`onnx`/`onnxruntime` adds back ~15 MB; nothing else changes
materially). The numpy and scipy lines are non-negotiable -- they're
load-bearing for the envelope detector and the resampling that feeds
every voter.

The torch + transformers line is **430 MB of disk used purely to
load three model checkpoints**. That is the single biggest target
for the slim plan.

### First-run model downloads

On the first detection call after a clean install, the runtime
fetches the following into shared system caches:

| Artifact            | Size | Cache location | Provenance |
| ------------------- | ---: | -------------- | ---------- |
| CLAP-HTSAT-unfused  | ~600 MB | `~/.cache/huggingface/hub/` | `transformers` -> HF Hub |
| CLIP-ViT (Voter E)  | ~600 MB | `~/.cache/huggingface/hub/` | `transformers` -> HF Hub |
| PANN CNN14          |  ~80 MB | `~/panns_data/` | `panns_inference` -> Google Drive |

That's **another ~1.3 GB** the user pays for on first detection. The
PANN download in particular is a known UX papercut: it lives in a
fixed `~/panns_data/` directory and is fetched from a Google Drive
URL that has been flaky.

### Cold start

Time-to-first-detection on a cold cache (everything downloaded
fresh) is dominated by:

1. `import torch` (~2.5 s).
2. `transformers` model load + tokenizer init (~6 s the first time,
   ~1.5 s subsequent loads).
3. PANN model download + load (~30+ s on a slow connection, ~2 s
   once cached).
4. Actual CLAP / PANN inference (small per-stage cost).

Outside the very first detection, steady-state cold start is
dominated by the torch import and the model load, totalling about
**4-6 seconds** before the API can serve a detection request. That
is the floor we need to beat in steady state.

## Slim target -- where the bytes go after the plan

### Wheel install, slim default

Target: **~100 MB on disk after `uv tool install splitsmith`**, no
detection run yet.

| Line                                | Disk size | Notes |
| ----------------------------------- | --------: | ----- |
| `onnxruntime` (CPU wheel)           |     15 MB | Replaces `torch` for prod inference |
| `huggingface_hub` (download utility)|    3-5 MB | Reused only for its `hf_hub_download` resume logic, not for HF Hub itself |
| `scipy`                             |     82 MB | Unchanged |
| `numpy`                             |     24 MB | Unchanged |
| `librosa`                           |    4.3 MB | Unchanged |
| Everything else (FastAPI, uvicorn, sklearn, joblib, soundfile, typer, rich, pyyaml, httpx, mcp, ...) | ~10-15 MB | sklearn and joblib stay; their wheels are small |
| **Total wheel surface**             | **~95-105 MB** | |

The two large lines that survive (scipy at 82 MB, numpy at 24 MB)
are not negotiable for the envelope detector and the resampling that
all voters depend on.

### First-run model cache

Target: **~180 MB under `~/.splitsmith/models/`** after first
detection.

| Artifact            | Size | Source |
| ------------------- | ---: | ------ |
| CLAP audio + text encoders (ONNX FP32) | ~80 MB | R2 |
| PANN CNN14 (ONNX FP32) | ~40 MB | R2 |
| CLIP visual encoder (ONNX FP32) | ~60 MB | R2 |
| **Total cache**     | **~180 MB** | |

How we get from 600 MB CLAP to 80 MB ONNX: the HF-distributed
checkpoint includes optimizer state, tokenizer assets in multiple
formats, and the full audio + text + projection towers in FP32. The
ONNX export keeps only the forward-pass weights for the audio
encoder + text encoder. Empirically the same kind of cut on similar
models lands at ~10-15% of the original size. We will validate the
exact number in the export step (doc 02).

Same story for CLIP (we only need the visual encoder; the text
encoder is dead weight for Voter E) and PANN (the bundled checkpoint
includes training state we never touch).

### Cold start, slim path

| Phase                                  | Today    | Slim target |
| -------------------------------------- | -------: | ----------: |
| Module imports (`torch` or `onnxruntime`) |   ~2.5 s |     <0.4 s |
| Model load (3 voter models)            |    ~3 s |     <0.5 s |
| Time-to-first-detection (cached)       | 4-6 s   |     ~1 s   |

The dominant win is **not** importing torch. `onnxruntime` cold
imports in tens of milliseconds and loads our three FP32 ONNX files
in well under half a second combined on Apple Silicon.

### After-first-run total

Wheel + model cache: **~100 MB + ~180 MB = ~280 MB**, which lands
inside the <300 MB locked-in target with some headroom for the
sklearn / joblib / soundfile lines drifting by a few MB.

## What changes vs what doesn't

### Stays exactly the same

- The Pydantic shapes that cross every module boundary
  (`MatchProject`, `StageProject`, `EnsembleConfig`, etc.).
- The 3-voter consensus described in `CLAUDE.md` and `SPEC.md`. The
  ensemble logic is unchanged; only the inference backend swaps.
- The calibration artifacts under `src/splitsmith/data/`. They gain
  SHA256 pins for the ONNX files they expect but the per-voter
  thresholds and apriori boosts are untouched.
- The Voter A envelope onset detector. It is pure DSP and does not
  go through torch or ONNX.
- The CLI surface. `splitsmith ui`, `splitsmith match ...`,
  `splitsmith compare ...` all behave the same. One new command
  appears (`splitsmith fetch-models`); see doc 04.
- The wire format produced by `/api/stages/{n}/shot-detect`. Shot
  arrays, voter signals, and consensus marks are byte-equivalent
  across backends within the parity tolerance defined in doc 05.

### Changes

- `features.py` and `visual.py` grow runtime backend selection (ONNX
  preferred, torch as a dev fallback). Detailed in doc 05.
- `pyproject.toml` moves torch / transformers / panns_inference into
  a `[dev]` optional-dependency extra. `onnxruntime` and
  `huggingface_hub` join the default deps. Detailed in doc 04.
- `scripts/build_ensemble_artifacts.py` learns to export ONNX in
  addition to its existing torch-only output. Detailed in doc 02.
- A new module `src/splitsmith/models/` owns manifest fetch,
  download, SHA256 verification, and cache lookup. Detailed in
  doc 03.
- A new CLI command `splitsmith fetch-models` lets users
  pre-download before their first detection. Detailed in doc 04.

### Goes away

- The implicit dependency on `~/.cache/huggingface/hub/` for the
  slim install. Existing dev installs still use the HF cache because
  they keep torch + transformers.
- The implicit dependency on `~/panns_data/` and the Google Drive
  fetch in `panns_inference.AudioTagging.__init__`.
- The 4-6 second cold start.

## Numbers we don't yet know

These are flagged as the things to measure during implementation,
not blockers to writing the plan:

- **Exact ONNX FP32 sizes for each export.** The 80 / 40 / 60 MB
  estimates are based on analogous exports; the actual numbers come
  from running `scripts/build_ensemble_artifacts.py --onnx` once we
  write it.
- **Cold-start time on Linux (Intel and arm64).** All measurements
  above are macOS arm64. The Linux numbers should be close but
  worth confirming once the slim wheel exists.
- **Numeric delta between torch and ONNX outputs.** We expect well
  under 1e-4 on cosine similarities and class probabilities, but
  this needs the parity test from doc 05 to confirm.
- **Whether INT8 quantisation is worth doing.** Halving the model
  sizes again to ~90 MB total cache is tempting, but only valuable
  if numeric drift stays inside the parity budget. Deferred to v2
  pending the FP32 measurements.

## How this doc gets used

01 is the doc you reread when someone argues "we should also remove
SciPy" or "we should bundle the models in the wheel". The numbers
here are the budget. If a proposed change blows the <300 MB target,
either find another line to cut or revisit the target.
