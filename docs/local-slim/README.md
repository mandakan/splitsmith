# Local slim

Architecture doc set for shrinking the Splitsmith local install to
something a user can land on their laptop in under a minute without
downloading a gigabyte of PyTorch wheels first.

The plan replaces `torch`, `transformers`, and `panns_inference` in
the production runtime with [ONNX Runtime](https://onnxruntime.ai/)
loading pre-exported model artifacts. The heavy ML libraries stay
available as `[dev]` extras for retraining and export. Models live
on a Splitsmith-controlled Cloudflare R2 bucket and are downloaded
once on first use, verified by SHA256, and cached under
`~/.splitsmith/models/`.

macOS and Linux are the v1 install targets. Windows is not foreclosed
but not in v1.

This plan complements -- it does not replace -- the
[saas-readiness](../saas-readiness/README.md) doc set. Both depend
on the same ONNX export pipeline. The local-slim work ships first
because it has no auth, storage, or billing dependencies; SaaS v1's
Tier 2 worker later reuses the same R2-hosted artifacts.

## Reading order

The set is meant to be read in order on the first pass. After that,
the docs are reference: jump to whichever covers the system you're
touching.

1. **[00 -- Context and principles](./00-context-and-principles.md)**
   Why the initiative exists, the working principle ("prod imports
   the minimum needed to run exported artifacts"), the 8-question
   decision record from the 2026-05-15 ideation, the locked-in
   v1/v2/v3 axes, and the derived principles every other doc
   respects.

2. **[01 -- Current footprint and targets](./01-current-footprint-and-targets.md)**
   The baseline measurements (where the 1.1 GB goes today), the slim
   target shape (~100 MB wheel + ~180 MB first-run cache), and what
   the cold-start budget looks like before and after. The reread doc
   when someone argues "we should also remove SciPy".

3. **[02 -- ONNX migration](./02-onnx-migration.md)**
   Per-voter export plan (CLAP, PANN, CLIP), target ONNX file
   inventory with expected sizes, the build script's new
   responsibilities, and the validation each export must pass before
   it ships.

4. **[03 -- Model hosting and delivery](./03-model-hosting-and-delivery.md)**
   The R2 bucket layout, manifest schema, content-addressing scheme,
   first-run download flow, on-disk cache layout, and the FastAPI
   server's "downloading models" UX.

5. **[04 -- Packaging and install](./04-packaging-and-install.md)**
   The `pyproject.toml` diff (what moves to `[dev]`, what's added),
   the optional-dependency extras (`[gpu]`, `[worker]`), the install
   story for `uv tool install splitsmith`, the ffmpeg system-dep
   handling, and the wheel build changes.

6. **[05 -- Dev vs prod parity](./05-dev-vs-prod-parity.md)**
   The two-backend selector, the parity test contract (per-voter
   tolerances, end-to-end consensus check), CI gating, and how
   contributors switch between ONNX and torch during model
   debugging.

7. **[06 -- Slim progress](./06-slim-progress.md)**
   Status doc with v1/v2/v3 checklists and an append-only decision
   log. Items flip from `[ ]` to `[x]` as work lands.

## Working principle (one-line restate)

Production runtime imports the minimum needed to run exported
artifacts: `onnxruntime` + numpy. Training and export are dev-only.
Heavy model weights live on a CDN, not in the wheel.

## Locked-in axes

Quick lookup -- full version with caveats lives in doc 00.

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

## What this doc set is not

- An implementation plan -- those live in GitHub issues per shipped
  doc.
- A model-quality plan -- it doesn't address retraining or model
  swaps, only the delivery surface for whatever checkpoint the
  ensemble currently uses.
- A SaaS plan -- that's [saas-readiness](../saas-readiness/README.md).

## Conventions

- **Numbering.** Two-digit prefixes (`00-` ... `06-`) so the docs
  sort correctly in directory listings.
- **Tone.** Prose first, tables second, code examples only when they
  pin a contract (a Python `Protocol`, a JSON shape, a CLI command).
- **Naming libraries and services.** Always with a link the first
  time they appear in a given doc.
- **Open questions live with their doc.** Cross-cutting open
  questions live in doc 00.
- **Status updates land in doc 06**, not in commit messages or PR
  descriptions. The decision log at the bottom of 06 is
  append-only.
