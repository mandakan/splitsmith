# 00 -- Context and principles

This document captures the **why** behind the local-slim initiative,
the principles that shape every other doc in this set, and the
decision record from the 2026-05-15 ideation conversation.

## Why this initiative exists

Splitsmith today installs via `pip install splitsmith` (or the
equivalent `uv tool install`). On a clean Python 3.11 environment that
pulls roughly **1.1 GB** of wheels -- dominated by PyTorch (~380 MB on
disk), SciPy (~82 MB), `transformers` (~50 MB), and NumPy (~24 MB).
On first detection the runtime then downloads another **~1.3 GB** of
ML weights into the Hugging Face cache and `~/panns_data/`:

- CLAP-HTSAT-unfused: ~600 MB
- CLIP visual probe (Voter E): ~600 MB
- PANN CNN14: ~80 MB

The result is a footprint that's fine for the user's own development
machine but actively hostile to the "let me try this on my laptop"
audience the marketing site is now aimed at. Install time is several
minutes on a fast connection, several gigabytes round-trip, and the
first detection call stalls for another download.

The goal of the local-slim initiative is to **collapse the install
surface to something on the order of a few hundred megabytes and a
single `uv tool install` command**, without regressing detection
accuracy and without forking the codebase. macOS and Linux are v1
targets; Windows is not foreclosed but not a v1 concern.

This is a complement to the existing
[saas-readiness](../saas-readiness/README.md) doc set, not a
replacement. The two share the same ML export pipeline (see Q4 in the
decision record below) but ship on independent timelines.

## Working principle (highest)

> **Production runtime depends on the minimum surface needed to run
> exported model artifacts. Training, calibration, and any other
> heavyweight tooling are dev-only and never imported by the prod
> path.**

Concretely:

- `torch`, `transformers`, and `panns_inference` move out of the
  default install. They stay available behind a `[dev]` extra for
  contributors who want to retrain models or run the export script.
- The prod path imports **`onnxruntime`** (~15 MB wheel) and loads
  ONNX-exported versions of CLAP, PANN, and CLIP. The same artifacts
  feed the hosted Tier 2 worker described in
  [saas-readiness/04-compute-backends.md](../saas-readiness/04-compute-backends.md).
- Model weights are not bundled in the wheel. They are downloaded
  once on first use from a Splitsmith-controlled CDN
  (Cloudflare R2 behind `splitsmith.app`), verified by SHA256, and
  cached under `~/.splitsmith/models/`.

This is the same off-the-shelf-first bias the SaaS doc set already
commits to. ONNX Runtime, `onnx`, `optimum`, `sklearn-onnx`, and
`huggingface_hub`'s download utility are all named, existing,
documented libraries. The R2 hosting decision is the only one that
introduces a new piece of infrastructure -- and that infrastructure is
already in the SaaS plan.

## Derived principles

These follow from the working principle and the locked-in decisions
in the record below. They shape the rest of the doc set.

1. **Detection accuracy never regresses.** Every model migration is
   gated by a numeric-parity test against the existing fixtures. A
   torch-vs-ONNX delta larger than 1e-4 on the relevant output
   triggers a halt, not a release.

2. **The dev path stays usable without ONNX.** Contributors training
   a new GBDT or experimenting with a new CLAP prompt set must not be
   forced to re-export between every iteration. `features.py` selects
   a backend at runtime: ONNX if `onnxruntime` is installed, torch if
   only `torch` is installed, and a clear error otherwise.

3. **First detection is the only network-required moment.** After
   model download, the app is fully offline. There is no telemetry
   ping, no licence check, no model-version probe. The CDN is hit
   exactly once per model artifact per machine, modulo manual
   `splitsmith fetch-models --force`.

4. **Model artifacts are content-addressed.** The CDN URL embeds the
   SHA256 (or a stable version slug that maps to one). A model swap
   is a new file at a new URL plus a manifest bump, not an in-place
   overwrite. Stale caches do not silently drift.

5. **ffmpeg stays a documented system dependency.** It is not
   bundled. The slim install assumes `brew install ffmpeg` or
   `apt install ffmpeg` and prints a friendly install hint if the
   binary is missing on first run.

6. **`pip` and `uv` are the only v1 distribution channels.** Homebrew
   formulas, single-file binaries, and Docker images are deferred
   until the slim PyPI install is validated. The SaaS doc set's own
   v1 invariants already constrain us to keep the codebase
   importable, so the slim version of the same wheel covers most of
   the cases a brew formula would.

## Decision record (2026-05-15 ideation)

The plan was shaped by two clarifying rounds. Capturing the questions
and answers here so future maintainers can see *how* the plan was
scoped, not just *what* the plan says.

### Round 1 -- foundational decisions

#### Q1. Install footprint target

**Answer: Pragmatic (<300 MB total).** A clean install plus the
first-run model download fits in roughly 280 MB:

- Slim wheel + deps: ~100 MB (no torch / transformers /
  panns_inference; ONNX Runtime instead).
- Model weights cached on first detection: ~180 MB (CLAP + PANN +
  CLIP, all ONNX FP32, no INT8 quantisation in v1).

The aggressive <100 MB target was rejected because SciPy alone is 82
MB and `librosa` requires its full surface. The conservative ~500 MB
target was rejected because bundling weights in a companion package
forecloses content-addressed updates and makes wheel version bumps
heavier than they need to be.

**Implication:** the existing torch + transformers + panns_inference
dependencies move to the `[dev]` extra. The prod path uses
`onnxruntime` plus `huggingface_hub` (for its `hf_hub_download`
helper, which we point at our own R2 URL).

#### Q2. Model delivery channel

**Answer: First-run download from a Splitsmith-controlled CDN
(Cloudflare R2 behind `splitsmith.app`).** The bucket is public-read,
backed by R2's CDN, and content-addressed by SHA256.

We considered Hugging Face Hub (free, CDN-backed, familiar API) and
GitHub Releases (zero ops). Both were rejected:

- HF Hub couples our release cadence to HF's policies and makes the
  download URL look like a third-party brand at the moment of first
  trust.
- GitHub Releases assets are awkward for files larger than 2 GB and
  harder to update independently of the wheel version.

R2 aligns with the SaaS doc set's storage pick. The `splitsmith.app`
domain (already deployed for the marketing site) gets a CNAME at
`models.splitsmith.app` pointing at the bucket's R2 public hostname.

**Implication:** we need a manifest file at a stable URL (e.g.
`https://models.splitsmith.app/manifest.json`) that maps model slugs
to specific SHA256-addressed object keys. The slim runtime reads the
manifest, picks the entries it needs, downloads, verifies, caches.

#### Q3. Codebase backend strategy

**Answer: Two backends, prod uses ONNX, dev uses either.** The
inference functions in `src/splitsmith/ensemble/features.py` and
`src/splitsmith/ensemble/visual.py` do runtime backend selection:

- If `onnxruntime` is importable and the relevant ONNX file is
  available, use it.
- Else if `torch` is importable and the original model weights are in
  the HF cache, use torch.
- Else raise a clear error that names both options and how to install
  each.

Training and export scripts under `scripts/` always import torch
directly -- they don't go through the backend selector.

We considered the strict separation (prod=ONNX only, dev=torch only)
and a single-runtime approach (ONNX everywhere). Both were rejected:

- Strict separation makes it hard for contributors to debug prod
  inference under torch when the ONNX export is suspect.
- ONNX-only training adds export round-trips to every model iteration
  and slows the contributor loop.

**Implication:** the calibration artifacts shipped under
`src/splitsmith/data/` grow a `voter_*.onnx.json` companion that
records the exported file's SHA256 and the expected manifest entry.
The build script (`scripts/build_ensemble_artifacts.py`) is extended
to optionally export ONNX alongside the existing torch path.

#### Q4. Relationship to the SaaS readiness plan

**Answer: Shared ML export pipeline, independent rollout.** Both
plans depend on the same "export models to ONNX" work. The slim
local app ships first because it is independent of auth, storage,
and billing. The hosted Tier 2 worker (described in
[saas-readiness/04-compute-backends.md](../saas-readiness/04-compute-backends.md))
reuses the same R2-hosted artifacts when SaaS v1 ships.

**Implication:** doc 02 of this set names the same ONNX target file
names that the SaaS worker will consume. The R2 bucket described in
doc 03 is the same bucket SaaS v1 will read from. The slim plan does
not assume the SaaS plan ships first or at all.

### Round 2 -- scope and entry-point decisions

#### Q5. v1 scope

**Answer: Replace torch + transformers + panns_inference in the prod
path, ship slim wheel.** v1 includes:

- ONNX exports for CLAP, PANN, and CLIP (Voter E). GBDT stays
  `.joblib` because it is already tiny.
- A `splitsmith.models` module that owns manifest fetch, download,
  SHA256 verification, cache lookup, and a friendly progress UI.
- `pyproject.toml` updates moving torch / transformers /
  panns_inference into a `[dev]` extra; `onnxruntime` and
  `huggingface_hub` added to the default deps.
- A `splitsmith fetch-models` CLI command for users who prefer to
  pre-download before their first detection (e.g. on a metered
  connection or a flight).
- Numeric-parity tests under `tests/` that run both backends against
  the existing fixtures and assert sub-1e-4 deltas on the
  voter outputs.

**Not in v1:** INT8 quantisation, brew formula, single-file binary,
Docker image, ffmpeg bundling, Windows packaging story.

#### Q6. Migration path for existing users

**Answer: Detect-and-prompt.** When a user upgrades from a torch
install to the slim install, their existing HF cache and
`~/panns_data/` directories stay on disk untouched. The first launch
of the slim app:

1. Checks for the new `~/.splitsmith/models/` cache.
2. If empty, prints a one-line message ("Splitsmith needs ~180 MB of
   ML models. Downloading now...") and proceeds.
3. Does not delete the legacy caches. A separate `splitsmith
   doctor --gc-legacy-cache` command (optional, not v1-blocking)
   handles cleanup later.

**Why not auto-cleanup:** the HF cache and `panns_data` are shared
across many tools on a developer's machine. Deleting them silently
would be hostile.

#### Q7. ffmpeg story

**Answer: Keep as a documented system dep.** Users install via brew
or apt. The slim install checks for the binary on first run and, if
missing, prints the exact install command for the user's OS. No
auto-download, no bundled wheel.

**Why not bundle:** `imageio-ffmpeg` adds ~70 MB and conflicts with
the <300 MB target. Auto-download adds licence-clean-build concerns
that aren't worth solving for a CLI tool that's expected to live
alongside other media tools (which also need ffmpeg).

#### Q8. Model versioning as the corpus grows

**Answer: Tie models to the wheel version; no active update check
in v1.** The shipped `ensemble_calibration.json` pins specific
SHA256s for every ONNX artifact it depends on. The slim runtime
downloads exactly those SHA256s from R2 (immutable, content-addressed
objects) and refuses to substitute a different version. New models
ship as a new wheel release that bundles a new calibration; the user
upgrade command is `uv tool upgrade splitsmith`, nothing else.

**Why this shape:**

- The calibration thresholds in `ensemble_calibration.json` are tuned
  to the exact outputs of specific model checkpoints. Swapping the
  CLAP weights under a fixed calibration silently degrades detection
  in ways the test suite would not catch.
- A single update path (the wheel) means there is only one thing to
  test, one thing to roll back, and one thing to explain to users.
- v1 stays fully offline after the first download. No background
  polling, no "update available" UI state, no manifest TTL to reason
  about.

**Deferred to v2:** an opt-in "beta channel" mode where the runtime
reads a separately-versioned manifest URL and pulls a paired
(model + calibration) bundle without a wheel bump. This is useful
once the audit corpus grows fast enough that wheel releases lag
model improvements. v1 reserves the design space by keeping the
manifest format extensible (see doc 03) but does not implement
channel selection.

**Implication for doc 03:** the bundled calibration carries enough
metadata (model slug, SHA256, expected size, optional source URL
override) to make the eventual channel mechanism a calibration-file
swap rather than a runtime rewrite.

## Locked-in decisions table

| Axis              | v1                                                 | v2                                  | v3+              |
| ----------------- | -------------------------------------------------- | ----------------------------------- | ---------------- |
| Footprint target  | <300 MB on-disk after first detection              | INT8 quantisation if practical      | --               |
| Inference runtime | ONNX Runtime in prod; torch in dev                 | --                                  | --               |
| Model delivery    | First-run download from R2 + SHA256 verify         | --                                  | --               |
| Distribution      | pip / uv (PyPI) only                               | Homebrew tap; single-file binary    | Windows packages |
| Model host        | R2 bucket behind `models.splitsmith.app`           | --                                  | --               |
| ffmpeg            | Documented system dep                              | Bundled if user feedback demands it | --               |
| Dev backend       | Either ONNX or torch via runtime selector          | --                                  | --               |
| GBDT delivery     | Ships in wheel as `.joblib` (already tiny)         | Optional `sklearn-onnx` export      | --               |
| Model versioning  | Pinned to wheel via bundled calibration; no auto-check | Opt-in "beta channel" manifest URL  | --               |
| Coupling to SaaS  | Same exported artifacts; independent rollout       | Shared model versioning             | --               |

## Open questions to validate during implementation

These are flagged here because they're cross-cutting; per-doc open
questions live in the relevant doc.

- **Optimum vs hand-written ONNX export for CLAP.** Hugging Face's
  `optimum.exporters.onnx` covers most transformers models, but
  CLAP-HTSAT-unfused has a custom audio tower. Validate that
  `optimum` handles it cleanly; if not, fall back to a manual
  `torch.onnx.export` script. Doc 02 has the details.
- **PANN CNN14 export path.** `panns_inference` ships only the
  PyTorch model. We may need to vendor the model definition in a
  small script that loads the checkpoint and exports. Verify the
  AudioSet ontology class index (427 for "Gunshot, gunfire") survives
  unchanged through the export.
- **First-run download UX in the FastAPI server context.** The CLI is
  easy: print a progress bar. The UI server is harder: the first
  detection call has to either block for the download or stream
  progress over an SSE channel. Pick when implementing
  doc 03.
- **GBDT export to ONNX.** `sklearn-onnx` exists and works for
  `GradientBoostingClassifier`. The motivation is dubious (the
  `.joblib` artifact is already <2 MB) but it would let us run a fully
  ONNX-only worker in SaaS Tier 2. Defer to a later round.

## What this doc set is NOT

- **An implementation plan.** Implementation lives in GitHub issues
  once a doc is approved. The doc set says *what* and *why*; issues
  say *how* and *when*.
- **A model-quality plan.** It does not address whether to retrain,
  switch model families, or quantise. It addresses how to deliver
  whatever model artifact the ensemble currently uses with a smaller
  install surface.
- **A SaaS doc set.** That is the
  [saas-readiness](../saas-readiness/README.md) set. This set is the
  smaller, earlier, independent piece.

## Reading order

1. **This doc (00)** -- you're here.
2. **01-current-footprint-and-targets.md** -- the baseline and the
   target shape, with numbers.
3. **02-onnx-migration.md** -- per-voter migration plan and the
   export script's responsibilities.
4. **03-model-hosting-and-delivery.md** -- R2 bucket layout, manifest
   schema, first-run download UX.
5. **04-packaging-and-install.md** -- `pyproject.toml` changes,
   extras, and the new install story.
6. **05-dev-vs-prod-parity.md** -- runtime backend selection,
   numeric-parity tests, CI gating.
7. **06-slim-progress.md** -- track shipped vs in-flight work.

After the first pass, the docs are reference: jump to whichever
covers the system you're touching.
