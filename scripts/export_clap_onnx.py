"""Export the CLAP audio encoder to ONNX and pre-encode the prompt bank.

Spike output for ``docs/local-slim/02`` -- drop torch + transformers
from the production runtime. This script runs offline against
``laion/clap-htsat-unfused`` and produces three artifacts under
``--out-dir`` (default ``build/onnx-spike/``):

* ``clap_audio.onnx`` -- the audio trunk + projection head, taking
  ``input_features`` of shape ``(B, 1, 1001, 64)`` (the shape
  ``ClapFeatureExtractor`` produces for a 1 s window at 48 kHz).
  External weight data is consolidated into a single file so slim
  users fetch one artifact per slug.
* ``clap_text_embeddings.npy`` -- L2-normalised text embeddings for
  the 10 prompts in ``CLAP_PROMPTS``, shape ``(10, D)``. The runtime
  never needs the text encoder once these are baked in.
* ``clap_sample_input_features.npy`` -- a fixed-seed
  ``(1, 1, 1001, 64)`` mel-spec produced by the real
  ``ClapFeatureExtractor``. Pinned now so a future follow-up PR can
  validate the slim runtime's numpy mel-spectrogram against it.

After export the script prints copy-pasteable ``model_artifacts``
snippets you paste into ``src/splitsmith/data/ensemble_calibration.json``
once the artifacts are uploaded to R2.

Note: this PR ships the export only. The ``load_clap_runtime`` ONNX
branch + numpy mel-spectrogram + parity test land in a follow-up.

Run:
    uv run python scripts/export_clap_onnx.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from splitsmith.ensemble.features import CLAP_MODEL_ID, CLAP_PROMPTS, CLAP_SR

OUT_DIR = Path("build/onnx-spike")
ONNX_OPSET = 17
AUDIO_SLUG = "clap_audio_encoder"
TEXT_SLUG = "clap_text_embeddings"
PLACEHOLDER_URL_BASE = "https://models.splitsmith.app/artifacts"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _consolidate_external(onnx_path: Path) -> None:
    """Merge external ``.data`` weights back into the ``.onnx`` file in place."""
    import onnx
    from onnx import TensorProto

    model = onnx.load(str(onnx_path), load_external_data=True)
    for init in model.graph.initializer:
        if init.data_location == TensorProto.EXTERNAL:
            init.data_location = TensorProto.DEFAULT
            del init.external_data[:]
    onnx.save(model, str(onnx_path), save_as_external_data=False)

    sidecar = onnx_path.with_suffix(onnx_path.suffix + ".data")
    if sidecar.exists():
        sidecar.unlink()


def _snippet(slug: str, path: Path) -> dict:
    sha256 = _sha256_file(path)
    return {
        slug: {
            "filename": path.name,
            "sha256": sha256,
            "size_bytes": path.stat().st_size,
            "url": f"{PLACEHOLDER_URL_BASE}/{sha256}/{path.name}",
        }
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out-dir", type=Path, default=OUT_DIR)
    p.add_argument("--opset", type=int, default=ONNX_OPSET)
    args = p.parse_args()

    import torch
    import torch.nn as nn
    from transformers import ClapModel, ClapProcessor

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading CLAP: {CLAP_MODEL_ID}")
    model = ClapModel.from_pretrained(CLAP_MODEL_ID)
    processor = ClapProcessor.from_pretrained(CLAP_MODEL_ID)
    model.eval()

    print("Pre-encoding text prompts")
    text_inputs = processor(text=list(CLAP_PROMPTS), return_tensors="pt", padding=True)
    with torch.no_grad():
        text_out = model.get_text_features(**dict(text_inputs))
    text_t = text_out.pooler_output if hasattr(text_out, "pooler_output") else text_out
    text_emb = text_t.cpu().numpy().astype(np.float32)
    text_emb = text_emb / (np.linalg.norm(text_emb, axis=1, keepdims=True) + 1e-9)
    text_path = args.out_dir / "clap_text_embeddings.npy"
    np.save(text_path, text_emb)
    print(f"  wrote {text_path}  shape={text_emb.shape}  dtype={text_emb.dtype}")

    print("Building sample input_features from a 1 s synthetic clip")
    rng = np.random.default_rng(0)
    sample_audio = rng.standard_normal(CLAP_SR).astype(np.float32) * 0.05
    feat_inputs = processor(audio=[sample_audio], sampling_rate=CLAP_SR, return_tensors="pt")
    sample_input_features = feat_inputs["input_features"].cpu().numpy().astype(np.float32)
    sample_path = args.out_dir / "clap_sample_input_features.npy"
    np.save(sample_path, sample_input_features)
    print(f"  wrote {sample_path}  shape={sample_input_features.shape}")

    class AudioTrunk(nn.Module):
        """Wraps ``get_audio_features`` so ONNX sees a single tensor in / out."""

        def __init__(self, clap: ClapModel) -> None:
            super().__init__()
            self.clap = clap

        def forward(self, input_features: torch.Tensor) -> torch.Tensor:
            out = self.clap.get_audio_features(input_features=input_features)
            return out.pooler_output if hasattr(out, "pooler_output") else out

    trunk = AudioTrunk(model).eval()
    dummy = torch.from_numpy(sample_input_features)
    with torch.no_grad():
        torch_out = trunk(dummy).cpu().numpy()
    print(f"  torch reference output shape={torch_out.shape}  dtype={torch_out.dtype}")

    onnx_path = args.out_dir / "clap_audio.onnx"
    print(f"Exporting ONNX -> {onnx_path}  (opset={args.opset})")
    torch.onnx.export(
        trunk,
        (dummy,),
        str(onnx_path),
        input_names=["input_features"],
        output_names=["audio_embedding"],
        dynamic_axes={
            "input_features": {0: "batch"},
            "audio_embedding": {0: "batch"},
        },
        opset_version=args.opset,
        do_constant_folding=True,
    )

    print("  consolidating external weight data into a single .onnx file")
    _consolidate_external(onnx_path)

    ref_path = args.out_dir / "clap_torch_reference.npy"
    np.save(ref_path, torch_out)
    print(f"  wrote {ref_path} for parity check")

    audio_sz = onnx_path.stat().st_size
    text_sz = text_path.stat().st_size
    print("\n  final artifacts:")
    print(f"    {onnx_path}  ({audio_sz/1024/1024:.1f} MiB)")
    print(f"    {text_path}  ({text_sz/1024:.1f} KiB)")

    snippets = {**_snippet(AUDIO_SLUG, onnx_path), **_snippet(TEXT_SLUG, text_path)}
    print("\n  model_artifacts snippet -- paste into ensemble_calibration.json:")
    print(json.dumps(snippets, indent=2))
    print(
        "\nDone. The ONNX runtime branch + numpy mel-spectrogram + parity "
        "test land in a follow-up PR; this PR ships only the exports."
    )


if __name__ == "__main__":
    main()
