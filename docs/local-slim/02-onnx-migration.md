# 02 -- ONNX migration

This doc plans the per-model migration from PyTorch to ONNX Runtime
for the prod inference path. It names tools, output file names, and
the validation each export must pass before it can ship.

The end state: the slim install imports only `onnxruntime` and
`numpy` at inference time. Torch is a `[dev]` dependency used by
`scripts/build_ensemble_artifacts.py` to produce the ONNX files and
to back the dev-mode runtime selector.

## What migrates

Three model families currently live behind torch + transformers +
panns_inference:

1. **CLAP-HTSAT-unfused** (Voter B + Voter C feature column).
   Loaded by `ensemble/features.py::load_clap_runtime`.
2. **PANN CNN14** (Voter C feature column, formerly Voter D).
   Loaded by `ensemble/features.py::load_pann_runtime`.
3. **CLIP-ViT-Base-Patch32** (Voter E).
   Loaded by `ensemble/visual.py::load_visual_runtime`.

Two model artifacts stay where they are:

- **Voter C GBDT** (`src/splitsmith/data/voter_c_gbdt.joblib`). The
  joblib file is under 2 MB and `sklearn` + `joblib` are already
  default dependencies. v2 may export to ONNX via `sklearn-onnx` for
  shared SaaS-worker reasons; doc 00's open-questions section
  flagged this. Not v1.
- **Voter E linear probe head** (`voter_e_visual_probe.joblib`).
  Same logic. The probe runs against the CLIP embedding output, so
  the migration question is "is the embedding source ONNX or torch";
  the linear head itself stays sklearn.

## Target ONNX file inventory

The exports land in `src/splitsmith/data/onnx/` during the build
script's run and are uploaded to R2 (doc 03). Their R2 keys are
content-addressed; the local filenames during build are stable
slugs.

| File                                | Source model                 | Expected size (FP32) | Inputs | Outputs |
| ----------------------------------- | ---------------------------- | -------------------: | ------ | ------- |
| `clap_audio_encoder.onnx`           | CLAP-HTSAT-unfused audio tower | ~40 MB | `(N, 480000)` float32 audio at 48 kHz | `(N, 512)` audio embeddings |
| `clap_text_embeddings.npy`          | CLAP-HTSAT-unfused text tower (pre-encoded) | <100 KB | n/a -- precomputed | `(P, 512)` text embeddings for the locked prompt bank |
| `pann_cnn14.onnx`                   | PANN CNN14 audio tagger      | ~40 MB | `(N, 32000)` float32 audio at 32 kHz | `(N, 527)` AudioSet clipwise probabilities |
| `clip_visual_encoder.onnx`          | openai/clip-vit-base-patch32 visual tower | ~60 MB | `(N, 3, 224, 224)` float32 image tensors | `(N, 512)` visual embeddings |

Note that for CLAP we **do not** ship the text encoder. The prompt
bank in `ensemble/features.py::CLAP_PROMPTS` is fixed at build time,
so the text embeddings are precomputed once and saved as a NumPy
array. This trades a small calibration-coupling cost (the prompt
bank is now part of the shipped artifacts, not a runtime
construction) for ~20 MB of weights we don't have to ship or load.

The same trick is **not** applied to CLIP because Voter E only uses
the visual encoder anyway -- there's no text side to precompute.

## Per-model migration plan

### CLAP-HTSAT-unfused

**Current code path.** `load_clap_runtime` calls
`ClapModel.from_pretrained(CLAP_MODEL_ID)` and
`ClapProcessor.from_pretrained(...)`. It then runs the text tower
once to cache `text_embeddings`. At detection time
`compute_clap_similarities` slices windows, calls the processor to
turn audio into mel features, and runs `get_audio_features`.

**Export approach.** Hugging Face's
[`optimum.exporters.onnx`](https://huggingface.co/docs/optimum/main/exporters/onnx/overview)
supports CLAP via the `clap` task. The build script invokes:

```python
from optimum.onnxruntime import ORTModelForAudioClassification  # or equivalent
# pseudo: optimum.exporters.onnx.main_export(
#   model_name_or_path=CLAP_MODEL_ID,
#   output=Path("src/splitsmith/data/onnx"),
#   task="zero-shot-audio-classification",
#   atol=1e-5,
# )
```

If `optimum` does not support CLAP cleanly (an open question in doc
00), the fallback is a manual `torch.onnx.export` against the audio
tower alone. The audio tower is a straightforward HTSAT transformer;
exporting it is a tracing operation, not a structural rewrite.

**Processor replacement.** The `ClapProcessor` does mel-spectrogram
computation. ONNX Runtime does not provide a built-in feature
extractor, so the slim runtime computes the mel features in NumPy
(via `librosa.feature.melspectrogram`, which is already a transitive
dependency). The build script captures the exact processor config
from CLAP and the slim runtime mirrors it. Numeric parity is part of
the parity test in doc 05.

**Text-embedding pre-bake.** During the build, after loading the
CLAP text tower, the script runs every entry in `CLAP_PROMPTS`
through the text encoder, normalises, and saves the resulting
`(P, 512)` matrix as `clap_text_embeddings.npy` alongside a JSON
that records the prompt order (so a prompt-order bug in the slim
runtime is immediately detectable).

**Slim runtime call site.** `compute_clap_similarities` becomes:

```python
mel = librosa_mel(windows, sample_rate=CLAP_SR)  # NumPy
audio_emb = clap_session.run(None, {"input": mel})[0]  # ORT
audio_emb /= np.linalg.norm(audio_emb, axis=1, keepdims=True)
return audio_emb @ runtime.text_embeddings.T
```

No `import torch` anywhere on this path.

### PANN CNN14

**Current code path.** `load_pann_runtime` instantiates
`panns_inference.AudioTagging(checkpoint_path=None, device="cpu")`,
which downloads the checkpoint from a Google Drive URL on first use.
`compute_pann_gunshot_probs` resamples to 32 kHz, slices windows,
calls `tagger.inference(batch)`, and extracts the
`Gunshot, gunfire` class column (index 427).

**Export approach.** `panns_inference` ships only the PyTorch
`Cnn14` module. The export is a vendored script that:

1. Downloads the `Cnn14_mAP=0.431.pth` checkpoint via the canonical
   Zenodo URL (or wherever the maintained mirror is at export
   time -- the build script is offline-friendly via a
   `--checkpoint-path` override).
2. Constructs the `Cnn14` module with the same hyperparameters
   `panns_inference` uses.
3. Loads the checkpoint into the module.
4. Traces a forward pass with a `(1, 320000)` dummy input and writes
   `pann_cnn14.onnx`.

The build script ships under `scripts/export_pann_to_onnx.py` so
contributors can re-run it if Zenodo URLs change.

**Class index pin.** The AudioSet ontology has been stable for years
and `Gunshot, gunfire` is index 427, but the build script writes the
index alongside the artifact in a `pann_cnn14.meta.json` so any
future shift in the index reflects in calibration metadata rather
than silently breaking detection.

**Slim runtime call site.** `compute_pann_gunshot_probs` becomes a
single `pann_session.run(None, {"input": batch})[0]` call followed
by a slice on the gunshot index. The resample step stays
`librosa.resample`.

### CLIP-ViT (Voter E)

**Current code path.** `visual.py::load_visual_runtime` calls
`CLIPModel.from_pretrained(...)` and `CLIPProcessor.from_pretrained(...)`,
then loads frames extracted from the video and runs the visual tower
on them.

**Export approach.** `optimum.exporters.onnx` supports CLIP visual
encoders directly. We export only the visual tower (the text tower
is unused) and the image processor's normalisation constants. The
slim runtime applies the normalisation in NumPy.

**Slim runtime call site.** `compute_clip_visual_embeddings` (the
analogous function the current code does inline) becomes:

```python
batch = preprocess_frames_to_tensor(frames)  # NumPy, mirrors CLIPProcessor
emb = clip_session.run(None, {"pixel_values": batch})[0]
emb /= np.linalg.norm(emb, axis=1, keepdims=True)
return emb
```

`load_visual_runtime` no longer detects MPS / CUDA / CPU. ONNX
Runtime providers are selected by the slim runtime layer based on
what's installed (see doc 05); for the user-facing slim install only
the CPU provider is configured, which is fast enough for the
per-candidate-frame call rate in this app.

## Build script changes (`scripts/build_ensemble_artifacts.py`)

The build script today is contributor-only -- it loads fixtures,
trains the GBDT, calibrates thresholds, and writes
`src/splitsmith/data/ensemble_calibration.json`. It already imports
torch.

After the migration it gains an additional responsibility: **for
each model family, optionally export the ONNX artifact and record
its SHA256 in the calibration JSON.**

New CLI surface for the script:

```
uv run scripts/build_ensemble_artifacts.py --onnx
```

This:

1. Runs the existing training + calibration pipeline. Unchanged.
2. After calibration, exports `clap_audio_encoder.onnx`,
   `clap_text_embeddings.npy`, `pann_cnn14.onnx`,
   `clip_visual_encoder.onnx` to `src/splitsmith/data/onnx/`.
3. Computes SHA256 for each file.
4. Writes a `model_artifacts` block into
   `ensemble_calibration.json` of the form:

   ```json
   {
     "model_artifacts": {
       "clap_audio_encoder": {
         "filename": "clap_audio_encoder.onnx",
         "sha256": "abcd1234...",
         "size_bytes": 41943040
       },
       "clap_text_embeddings": { ... },
       "pann_cnn14": { ... },
       "clip_visual_encoder": { ... }
     }
   }
   ```

5. Optionally (`--upload`) pushes the ONNX files to the R2 bucket
   under their content-addressed keys (see doc 03).

The slim runtime never re-runs the build script. It only reads the
`model_artifacts` block from the bundled `ensemble_calibration.json`
and asks the model-delivery layer to materialise each artifact.

## Validation each export must pass

Before any new ONNX artifact ships:

1. **Numeric parity.** A test under
   `tests/test_onnx_parity.py` loads the same fixture audio, runs it
   through both the torch path and the ONNX path, and asserts the
   per-candidate similarity arrays / probability arrays differ by
   less than `1e-4` on L_inf. Doc 05 has the full parity contract.
2. **Size budget.** The export file must fit the budget in the
   inventory table above (with 20% headroom). A blown budget is
   either a model change that needs a calibration re-tune or a sign
   the export accidentally included weights it shouldn't.
3. **End-to-end fixture eval.** The full ensemble runs against the
   audited fixture set under the new artifact and must match the
   shipped detection counts to within a single-candidate tolerance
   per stage. This catches threshold drift the numeric parity test
   misses (e.g. tiny similarity shifts that nudge a borderline
   candidate across `voter_b_threshold`).

Anything failing any of these gates blocks the artifact. No flag
disables the gates; the build script aborts.

## What this doc deliberately does not cover

- **Quantisation.** INT8 dynamic / static quantisation is a v2 path.
  It halves model sizes again but is a separate parity-and-accuracy
  conversation. Doc 00's locked-in table lists it as v2.
- **GPU inference for the slim install.** The slim runtime ships
  with the CPU-only `onnxruntime` wheel. Users on Apple Silicon get
  fine performance through the default execution provider; users
  with NVIDIA GPUs who want CUDA can install
  `onnxruntime-gpu` themselves via the dev extra, and the runtime
  selector picks it up.
- **Model retraining.** Out of scope. This doc is about delivering
  the existing checkpoints with a smaller install surface.

## Open questions

- **Optimum coverage for CLAP.** Verify before committing to the
  `optimum` path. If it doesn't cover CLAP cleanly, the fallback
  manual export adds maybe 100 lines to
  `scripts/build_ensemble_artifacts.py` but doesn't change the rest
  of the plan.
- **CLAP processor parity.** The mel-spectrogram computation in
  `librosa` and in `transformers.ClapProcessor` are not identical
  bit-for-bit. The parity test must run against the processor's
  output to detect any drift. We may need a small NumPy mel module
  that mirrors `ClapProcessor` exactly rather than reusing librosa.
- **Whether to keep the visual frame cache key scheme unchanged.**
  Today `visual.py` keys the on-disk frame cache by
  `(absolute_path, size, mtime_ns)`. The slim path doesn't need to
  change this, but if we want hosted-mode-friendly remote video
  fingerprinting later, the key scheme moves into a small helper.
  Out of scope for v1.
