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

- [x] `src/splitsmith/ensemble/backend.py` with `Backend` enum +
  `select_backend()`. **PR #378.** Preference: torch wins when both
  installed (dev path with model caches); ONNX wins when only
  onnxruntime is installed (slim wheel). End users on the slim wheel
  reach the ONNX branch by elimination.
- [x] `load_clap_runtime`, `load_pann_runtime`,
  `load_visual_runtime` return typed runtime dataclasses with numpy
  callables. **PR #378.**
- [x] `compute_*` functions go through the typed callable; no
  module-level torch imports. **PR #378.**
- [x] `tests/test_no_torch_in_prod_path.py` sentinel. **PR #378.**

### ONNX exports

- [x] PANN CNN14 export (`scripts/export_pann_onnx.py`) -- raw audio
  in, gunshot probability out; mel-spec layer baked into the graph.
  **PR #381 + #84 spike.**
- [x] CLAP audio encoder export + pre-baked text embeddings
  (`scripts/export_clap_onnx.py`). **PR #382.**
- [x] Pure-numpy mel-spectrogram for CLAP (no Apache-2.0 vendoring;
  uses BSD-3 librosa + numpy primitives). Parity vs
  `ClapFeatureExtractor`: L_inf 3.8e-6 dB. **PR #383.**
- [-] CLIP visual encoder export. Deferred to v2 with Voter E ONNX
  migration; Voter E is off by default (`enable_voter_e=False`,
  gated by `SPLITSMITH_ENABLE_VOTER_E`) and explicitly out of slim
  v1. To use Voter E today, install dev extras
  (`uv sync --all-groups`) so the torch backend stays available.
- [ ] `--upload` step on the export scripts: SHA256 + size +
  filename + R2 URL written into a `model_artifacts` block in
  `ensemble_calibration.json` and uploaded via `wrangler r2 object
  put`. Manual snippet emission already works; wrapper still TODO.

### Parity testing

- [x] `tests/test_onnx_parity.py` for PANN + CLAP. Each test
  pytest.skip()s when its env-var-resolved artifact isn't present so
  CI without the export-time toolchain still runs cleanly. **PR
  #381 + #383.**
- [ ] End-to-end full-ensemble parity test once R2 + artifact upload
  lands so CI can pull artifacts on demand.
- [ ] Wire the parity tests into the dev-extras CI matrix (today
  they run locally with env vars set).

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

- [x] `src/splitsmith/models/` module: manifest parsing, on-disk
  cache layout, SHA256 streaming verify, exclusive cross-process
  lock. **PR #379.**
- [x] httpx-only resumable download with retry on transient network
  failure; no retry on hash mismatch. **PR #379.** (huggingface_hub's
  helper deferred -- httpx covers the v1 contract.)
- [x] Typed failure modes (`NetworkUnreachable`, `HttpError`,
  `HashMismatch`). **PR #379.**
- [x] `splitsmith fetch-models --list / --verify / --force` CLI.
  **PR #379.**
- [x] `GET /api/models/status` endpoint reports per-artifact
  present / missing / mismatched. Frontend overlay still TODO; the
  endpoint is in place for the SPA to consume. **PR #379.**

### `pyproject.toml` changes

- [x] `torch`, `transformers`, `panns-inference` moved to
  `[dependency-groups] dev`. **PR #384.**
- [x] `onnxruntime>=1.20.0` + `huggingface_hub>=0.26.0` promoted to
  `[project] dependencies`. **PR #384.**
- [x] `onnx` + `onnxscript` kept in `[dev]` alongside torch for the
  export scripts + parity tests. **PR #384.**
- [-] `[project.optional-dependencies] gpu = ["onnxruntime-gpu"]`.
  Deferred until measured demand from a CUDA user; CPU path covers
  the desktop app at responsive cold-start times.
- [-] `[tool.hatch.build.targets.wheel] exclude` for `**/data/onnx/**`.
  Not needed yet -- no ONNX files live under `src/splitsmith/data/`.
  Reconsider when the R2 upload tooling lands.

### ffmpeg + install UX

- [x] First-launch ffmpeg check + 24h positive-result cache in
  `state.json`; platform-aware install hints (darwin / linux /
  win32). **PR #380.**
- [ ] README install section rewrite around `uv tool install
  splitsmith` + optional `splitsmith fetch-models`. Pending the R2
  hosting phase landing so the upgrade story is true.

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
