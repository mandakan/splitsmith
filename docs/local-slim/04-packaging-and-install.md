# 04 -- Packaging and install

This doc spells out the `pyproject.toml` changes, the optional
dependency extras, and the install story the slim plan ships. The
target is "`uv tool install splitsmith` lands a ~100 MB wheel that
works offline after one ~180 MB first-run download".

## Default dependencies after the slim refactor

The proposed `[project] dependencies` list:

```toml
dependencies = [
    "librosa>=0.10.2",
    "numpy>=1.26.0",
    "scipy>=1.13.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.6.0",
    "typer>=0.12.0",
    "rich>=13.7.0",
    "pyyaml>=6.0.1",
    "soundfile>=0.12.1",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "httpx>=0.28.0",
    "python-dotenv>=1.0.0",
    "scikit-learn>=1.8.0",
    "joblib>=1.4.0",
    # Slim runtime inference + model download (doc 02 + doc 03).
    "onnxruntime>=1.20.0",
    "huggingface_hub>=0.26.0",
    # Model Context Protocol server (issue #211).
    "mcp>=1.0.0",
]
```

Versus today's list, the changes are:

- **Removed:** `torch`, `panns-inference`, `transformers`. None of
  these are required for the prod inference path after the ONNX
  migration.
- **Added:** `onnxruntime`, `huggingface_hub`. `onnxruntime` ships
  the inference engine; `huggingface_hub` ships the resumable
  download helper we point at our own R2 URL.

Wheel-size impact (doc 01 has the table):

- `torch` -- 380 MB removed.
- `transformers` -- 50 MB removed.
- `panns-inference` -- ~70 KB removed; the saving here was indirect
  (it pulls torch).
- `onnxruntime` -- ~15 MB added.
- `huggingface_hub` -- ~3-5 MB added.

Net: ~430 MB removed, ~20 MB added. Total wheel surface drops from
~1.1 GB to ~100 MB.

## The `[dev]` extra

Contributors retraining models or running the export script still
need torch + transformers + panns_inference. They move from the
default dependency block into the existing `[dependency-groups] dev`
group, joining the testing and linting tools already there:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.4.0",
    "black>=24.0.0",
    "mypy>=1.10.0",
    "respx>=0.21.1",
    "pyarrow>=17.0.0",
    "matplotlib>=3.9.0",
    # ML training + export. Production runtime uses onnxruntime
    # against the artifacts the build script emits.
    "torch>=2.11.0",
    "panns-inference>=0.1.1",
    "transformers>=5.7.0",
    # Tooling for exporting CLAP / CLIP to ONNX.
    "optimum>=1.20.0",
    "onnx>=1.16.0",
]
```

Contributors run `uv sync --all-groups` (or `uv sync --group dev`)
to install everything. End users running `uv tool install splitsmith`
or `pip install splitsmith` never see these wheels.

Note that `optimum` and `onnx` join the dev group. They are only
needed by `scripts/build_ensemble_artifacts.py --onnx` and the
parity tests. The prod path uses `onnxruntime`, which is a separate
wheel and doesn't depend on `onnx`.

## Optional consumer extras

Two end-user-facing extras are introduced:

```toml
[project.optional-dependencies]
# GPU inference via CUDA. Linux + NVIDIA only; macOS users get
# Apple Silicon acceleration through onnxruntime's CoreML provider
# without any extra install.
gpu = ["onnxruntime-gpu>=1.20.0"]

# Audio-only minimal install for hosted Tier 2 workers. Drops
# fastapi/uvicorn/mcp/etc. and keeps just the ensemble pipeline.
# Mirrors the SaaS readiness "compute backend" surface (see
# ../saas-readiness/04-compute-backends.md). Off by default; the
# default install still includes the UI.
worker = []  # placeholder; doc 05 of saas-readiness will populate
```

Users installing for the first time don't need to think about
extras. `uv tool install splitsmith` does the right thing; the
extras are escape hatches for special cases.

`onnxruntime` and `onnxruntime-gpu` cannot be installed in the same
environment (they conflict on the `onnxruntime` package name). The
`gpu` extra is therefore an explicit replacement, not an addition:

```
uv tool install splitsmith[gpu]
```

The runtime selector in doc 05 picks the CUDA provider when
`onnxruntime-gpu` is present.

## Install story

### macOS + Linux, slim default

```
$ uv tool install splitsmith
   ...
Installed splitsmith v0.2.0 (~95 MB)

$ splitsmith --help
   ...

$ splitsmith ui
[splitsmith] checking system dependencies... ffmpeg ✓
[splitsmith] starting on http://127.0.0.1:8080
```

First detection in the UI triggers the download (see doc 03's UX
section) and shows the progress overlay. Subsequent detections are
offline.

### Pre-fetching models

```
$ splitsmith fetch-models
Downloading clap_audio_encoder (40 MB)...   ✓
Downloading clap_text_embeddings (96 KB)... ✓
Downloading pann_cnn14 (40 MB)...           ✓
Downloading clip_visual_encoder (60 MB)...  ✓
All models cached at /Users/you/.splitsmith/models/
```

The `splitsmith fetch-models` command (doc 03) is fully optional;
the runtime fetches on demand. Users on metered connections or
about to go offline pre-fetch.

### Dev install

```
$ git clone https://github.com/mathiasa/splitsmith
$ cd splitsmith
$ uv sync --all-groups
```

This installs the slim default deps **plus** torch + transformers +
panns-inference + optimum + onnx + the test / lint tooling.
Contributors can then run:

- `uv run pytest` -- exercises both ONNX and torch paths.
- `uv run scripts/build_ensemble_artifacts.py --onnx` -- retrains
  GBDT, recalibrates thresholds, exports ONNX artifacts, and
  optionally uploads to R2.

### ffmpeg

Slim install checks for `ffmpeg` on first launch and, if missing,
prints:

```
Splitsmith needs ffmpeg to extract audio. Install it with:

    macOS:   brew install ffmpeg
    Ubuntu:  sudo apt install ffmpeg
    Fedora:  sudo dnf install ffmpeg

Then re-run `splitsmith ui`.
```

No auto-install (per doc 00, Q7). The check happens once at startup;
subsequent invocations skip it after a positive result is cached in
`~/.splitsmith/state.json` for 24h.

## Wheel build (`hatch`) changes

The current `[tool.hatch.build.targets.wheel]` block excludes
frontend dev artefacts but does include the built SPA at
`src/splitsmith/ui_static/dist/`. The slim plan keeps the same
shape, with two additions:

1. The ONNX artifacts produced by
   `scripts/build_ensemble_artifacts.py --onnx` land in
   `src/splitsmith/data/onnx/` for local development. These files
   are **excluded from the wheel** -- they live on R2, not in the
   wheel.
2. The bundled `ensemble_calibration.json` keeps shipping in the
   wheel under `src/splitsmith/data/`. Its new `model_artifacts`
   block is what tells the slim runtime which SHAs to fetch.

Updated `[tool.hatch.build.targets.wheel]` exclude list:

```toml
exclude = [
  "**/ui_static/node_modules",
  "**/ui_static/src",
  "**/ui_static/package.json",
  "**/ui_static/pnpm-lock.yaml",
  "**/ui_static/vite.config.ts",
  "**/ui_static/tsconfig*.json",
  "**/ui_static/index.html",
  "**/ui_static/.gitignore",
  "**/ui_static/dist/**/*.map",
  # Slim plan (doc 04): ONNX artifacts ship via R2, not the wheel.
  "**/data/onnx/**",
]
```

`ensemble_calibration.json` and the existing `.joblib` files stay
included (they are tiny and load-bearing).

## Migration for existing users

The slim plan ships in a wheel version bump (e.g. v0.1.x ->
v0.2.0). Users running `uv tool upgrade splitsmith` get:

1. The new slim wheel (~95 MB).
2. On first detection after the upgrade, a ~180 MB download into
   `~/.splitsmith/models/`.

Their legacy state survives untouched:

- `~/.cache/huggingface/hub/` -- left in place. Other tools may use
  it. A future `splitsmith doctor --gc-legacy-cache` (not v1) helps
  users clean it up if they want the disk space back.
- `~/panns_data/` -- left in place. Same logic.

The slim runtime never reads either legacy location. There is no
data migration -- the legacy caches and the new `~/.splitsmith/`
cache contain different artifact formats.

If a user wants to downgrade to the pre-slim wheel, `uv tool
install splitsmith==0.1.x` works exactly as before; the legacy
caches are still on disk and the old runtime picks them up.

## CI implications

CI today exercises the full torch + transformers + panns_inference
path. Two new CI jobs:

1. **Slim wheel build + smoke.** `uv build`, install the wheel into
   a fresh venv, run `splitsmith fetch-models` against a CI-local
   model mirror (a staging R2 bucket or a stubbed httpx server),
   run a tiny detection fixture, assert output matches the
   committed reference.
2. **Parity test job.** Runs `pytest tests/test_onnx_parity.py`
   against both backends. This requires both `torch` and
   `onnxruntime` installed, so it lives in the dev-extra-installed
   matrix. Doc 05 has the test contract.

The existing test suite continues to run on the dev install. No
test is removed.

## Open questions

- **Whether to add a `splitsmith doctor` command in v1.** A
  diagnostic command (checks ffmpeg, checks cache integrity, prints
  versions, prints config paths) would be the natural home for the
  "clean up legacy caches" affordance and for the "verify my
  install" affordance. Probably not strictly v1, but cheap.
- **`uv tool install` vs `pipx install` UX parity.** Both work
  identically for this wheel. README guidance: prefer `uv tool
  install` because it is faster and resolves dependencies more
  predictably. No code change needed.
- **Wheel platform tags.** Today's wheel is `py3-none-any`. The
  slim wheel can stay platform-independent because
  `onnxruntime` and `librosa` ship their own platform-specific
  wheels via their existing release process. We do not introduce a
  cibuildwheel pipeline.
