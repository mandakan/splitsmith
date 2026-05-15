# 06 -- Slim progress

This is the **status doc**. It tracks what's shipped, what's in
flight, and what's deferred for the local-slim plan.

Update this file as work lands. The other docs (00-05) describe the
target shape; this doc is the current state.

## How to read this doc

Each milestone has a checklist. Items use one of four marks:

- `[x]` -- shipped on the named branch / PR
- `[~]` -- in flight (work started, not merged)
- `[ ]` -- not started
- `[-]` -- explicitly deferred (with a note saying when to reconsider)

Each shipped item links to the PR. Each in-flight item links to the
issue or branch.

When this file is updated, the date at the top of the relevant
section is bumped.

## v1 -- slim PyPI install, ONNX prod runtime

Goal (per doc 00, Q5): replace torch + transformers +
panns_inference in the prod path with ONNX Runtime + first-run model
download, ship a slim wheel that installs in under 100 MB, and lands
detection inside the <300 MB on-disk target.

**Last updated:** 2026-05-15 (initial draft of doc set).

### Foundation -- backend abstraction in the codebase

These can ship before any ONNX artifact exists. Each is a refactor
that makes today's torch code pass through a backend selector
without behavioural change.

- [ ] Add `src/splitsmith/ensemble/backend.py` with the `Backend`
  enum and `select_backend()`. ONNX path raises NotImplementedError
  for now. (See doc 05.)
- [ ] Refactor `load_clap_runtime`, `load_pann_runtime`,
  `load_visual_runtime` to return a typed runtime dataclass with
  numpy-in / numpy-out callables. Today's torch implementation
  becomes the torch branch of the selector. (See doc 05.)
- [ ] Refactor `compute_clap_similarities`,
  `compute_pann_gunshot_probs`, and CLIP visual inference to call
  the typed callable rather than the model directly. No `import
  torch` in these functions. (See doc 02.)
- [ ] Add `tests/test_no_torch_in_prod_path.py` -- patches
  `sys.modules['torch']` to None and runs a tiny detection. Becomes
  a regression sentinel for accidental torch imports. (See doc 05.)

### ONNX exports

- [ ] Extend `scripts/build_ensemble_artifacts.py` with a `--onnx`
  flag that exports `clap_audio_encoder.onnx`,
  `clap_text_embeddings.npy`, `pann_cnn14.onnx`,
  `clip_visual_encoder.onnx`. (See doc 02.)
- [ ] Pre-bake CLAP text embeddings from the locked prompt bank;
  write `clap_text_embeddings.npy` and a sibling JSON with prompt
  order. (See doc 02.)
- [ ] Vendor a small PANN CNN14 export script that loads the
  upstream `Cnn14_mAP=0.431.pth` checkpoint and traces a
  `(1, 320000)` forward pass to ONNX. (See doc 02.)
- [ ] Validate `optimum.exporters.onnx` covers CLAP cleanly. If not,
  add a manual `torch.onnx.export` fallback. (Doc 00 open question.)
- [ ] Write a NumPy mel-spectrogram helper that matches
  `transformers.ClapProcessor`'s output exactly. (See doc 02 open
  questions; required for parity.)
- [ ] Add `model_artifacts` block writer to the build script -- per
  artifact: filename, sha256, size, R2 URL. Writes into
  `src/splitsmith/data/ensemble_calibration.json`. (See doc 02.)

### Parity testing

- [ ] Add `tests/test_onnx_parity.py` with per-voter L_inf checks
  against the existing fixtures. Tolerances from doc 05. (See doc 05.)
- [ ] Add the end-to-end parity test: full ensemble on each fixture,
  compare consensus shot times exact, signal arrays within ±15 ms.
  (See doc 05.)
- [ ] Wire the parity tests into the dev-extras CI matrix. (See
  doc 04.)

### Model hosting infrastructure

- [ ] Provision `splitsmith-models` R2 bucket in the Cloudflare
  account that already owns `splitsmith.app`. (See doc 03.)
- [ ] Add `models.splitsmith.app` CNAME pointing at the bucket; set
  cache headers per doc 03 (immutable on artifacts, 300s on
  manifest).
- [ ] Write the maintainer upload tooling -- a thin wrapper around
  `wrangler r2 object put` invoked by the build script's `--upload`
  flag. (See doc 02 + doc 03.)
- [ ] Author the initial `manifest.json` from the first ONNX export
  and upload it.

### Slim runtime model layer

- [ ] Add `src/splitsmith/models/` module: manifest schema parsing,
  download with sha256 verification, on-disk cache layout under
  `~/.splitsmith/models/`, lock file. (See doc 03.)
- [ ] Use `huggingface_hub`'s download helper (pointed at our own
  URL) for resumable HTTP + etag caching. (See doc 03.)
- [ ] Implement typed failure modes (`NetworkUnreachable`,
  `HttpError`, `HashMismatch`) and clear user-facing messages.
  (See doc 03.)
- [ ] Add the `splitsmith fetch-models` CLI command with
  `--verify` / `--force` / `--list` flags. (See doc 03 + doc 04.)
- [ ] Wire the FastAPI server's `/api/models/status` endpoint and
  the frontend overlay that shows download progress when artifacts
  are missing or in-flight. (See doc 03.)

### `pyproject.toml` changes

- [ ] Remove `torch`, `transformers`, `panns-inference` from
  `[project] dependencies`. (See doc 04.)
- [ ] Add `onnxruntime>=1.20.0` and `huggingface_hub>=0.26.0` to
  `[project] dependencies`. (See doc 04.)
- [ ] Move `torch`, `transformers`, `panns-inference` into
  `[dependency-groups] dev`; add `optimum` and `onnx`. (See doc 04.)
- [ ] Add `[project.optional-dependencies] gpu =
  ["onnxruntime-gpu"]`. (See doc 04.)
- [ ] Update `[tool.hatch.build.targets.wheel] exclude` to drop
  `**/data/onnx/**`. (See doc 04.)

### ffmpeg + install UX

- [ ] First-launch ffmpeg check with friendly install instructions
  per OS. Cache the positive result in
  `~/.splitsmith/state.json` for 24h. (See doc 04.)
- [ ] README install section rewrite: `uv tool install splitsmith`
  as the primary path; `splitsmith fetch-models` as the optional
  pre-fetch step. (See doc 04.)

### Release gating

- [ ] CI slim-wheel job: `uv build`, install into clean venv, run
  `splitsmith fetch-models` against staging mirror, run smoke
  detection. (See doc 04.)
- [ ] Release script refuses to publish if calibration's
  `model_artifacts.*.sha256` references SHAs not present in the
  R2 bucket. (See doc 04 + doc 05.)

## v2 -- decoupled model channels + quantisation

Goal: support model updates without wheel releases for the audit
corpus's typical update cadence, and shrink the model cache further
with INT8 quantisation.

**Last updated:** 2026-05-15 (initial draft).

- [-] Opt-in "beta channel" mode: runtime reads a separate manifest
  URL and pulls a paired (model + calibration) bundle. Reconsider
  when wheel release cadence demonstrably lags model improvements.
  (See doc 00 Q8.)
- [-] INT8 quantisation via `onnxruntime.quantization`. Reconsider
  after FP32 parity has been measured for a release cycle.
  (See doc 02.)
- [-] `splitsmith doctor` command for cache diagnostics, ffmpeg
  check, version dump, legacy cache cleanup. (See doc 04 open
  questions.)
- [-] Strict-parity debug mode (`--strict-parity`) for paranoid CI.
  (See doc 05 open questions.)
- [-] Apple Silicon CoreML execution provider, contingent on
  measured numeric drift staying inside the parity budget.
  (See doc 05 open questions.)

## v3+ -- platform expansion

- [-] Homebrew tap (`brew install splitsmith/splitsmith/splitsmith`).
  Reconsider when there is demonstrated PyPI install friction the
  brew formula would solve.
- [-] Single-file binary releases via PyInstaller or Nuitka.
  Reconsider if "must not require Python" becomes a real ask from
  users.
- [-] Windows packaging. Reconsider if Windows shows up in real
  usage analytics.
- [-] Docker image for Linux power users. Reconsider only if
  hosting users explicitly ask for it.

## Decision log

Append-only. Each entry: date, decision, reason, link to the doc /
section that owns the consequence.

### 2026-05-15 -- initial doc set drafted

- **Target footprint <300 MB after first detection.** Pragmatic
  middle ground between the aggressive <100 MB (impossible because
  scipy alone is 82 MB) and the conservative ~500 MB (forecloses
  content-addressed model updates).
  → doc 00 Q1, doc 01.
- **Cloudflare R2 behind `models.splitsmith.app`.** Aligns with the
  SaaS doc set's storage pick. Rejected HF Hub (third-party brand
  at moment of first trust) and GitHub Releases (>2 GB file
  awkwardness).
  → doc 00 Q2, doc 03.
- **Two backends, prod=ONNX, dev=either.** Strict separation
  rejected because contributors need to debug prod inference under
  torch. ONNX-only training rejected because it slows the
  contributor loop.
  → doc 00 Q3, doc 05.
- **Shared ML export pipeline with SaaS, independent rollout.**
  Same R2 artifacts feed both the slim local app and the future
  hosted Tier 2 worker. Slim ships first because it has fewer
  external dependencies.
  → doc 00 Q4, doc 02 + doc 03 cross-references.
- **v1 scope locked.** Replace torch + transformers +
  panns_inference in prod; ship slim wheel + first-run download.
  No INT8, no brew, no binary, no Docker, no Windows, no ffmpeg
  bundling.
  → doc 00 Q5.
- **No active update check in v1.** Models tied to wheel version
  via bundled calibration's pinned SHAs. `uv tool upgrade
  splitsmith` is the only update path. Reserve `channels` /
  `base_url` in the manifest schema for v2 decoupled-channel mode.
  → doc 00 Q8, doc 03 manifest schema.
- **pip / uv only as v1 distribution.** Brew, single-file binaries,
  and Docker deferred. The slim PyPI install should land most of
  the "convenient install" wins on its own.
  → doc 00, doc 04.
- **ffmpeg stays a documented system dep.** Bundling
  `imageio-ffmpeg` conflicts with the <300 MB target.
  → doc 00 Q7, doc 04.
