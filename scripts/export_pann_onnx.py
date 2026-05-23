"""Export the PANN Cnn14 audio tagger to a self-contained ONNX file.

Lands the slim-runtime ONNX path for Voter D (PANN gunshot probability)
per docs/local-slim/02. The original spike (PR #84) tested only the
shape and dtype of the export; this version consolidates external
weight data into a single ``.onnx`` file so the slim model registry
can fetch one artifact rather than a graph + sidecar pair.

What it writes
--------------
``--out-dir`` defaults to ``build/onnx-spike/`` and ends up with:

* ``pann_cnn14.onnx`` -- self-contained graph + weights (~312 MB FP32).
  Input ``audio`` shape ``(batch, samples)`` at 32 kHz; output
  ``clipwise_output`` shape ``(batch, 527)``. Mel-spec layer baked in.
* ``pann_sample_audio.npy`` / ``pann_torch_reference.npy`` -- fixed-seed
  parity inputs used by ``verify_onnx_parity.py`` and
  ``tests/test_onnx_parity.py``.

After export the script prints a copy-pasteable ``model_artifacts``
JSON snippet (slug = ``pann_cnn14``) you paste into
``src/splitsmith/data/ensemble_calibration.json`` once the artifact is
uploaded to R2 (placeholder URL until then).

Run:
    uv run python scripts/export_pann_onnx.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from splitsmith.ensemble.features import PANN_SR

OUT_DIR = Path("build/onnx-spike")
ONNX_OPSET = 17
SAMPLE_LEN_S = 1.0
ARTIFACT_SLUG = "pann_cnn14"
PLACEHOLDER_URL_BASE = "https://models.splitsmith.app/artifacts"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _consolidate_external(onnx_path: Path) -> None:
    """Merge external ``.data`` weights back into the ``.onnx`` file in place.

    PyTorch's dynamo-based ``torch.onnx.export`` writes a small graph
    file with weights in a sidecar ``<name>.data``. Slim users get one
    artifact per slug, so we re-save with ``save_as_external_data=False``
    and delete the sidecar.
    """
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


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out-dir", type=Path, default=OUT_DIR)
    p.add_argument("--opset", type=int, default=ONNX_OPSET)
    args = p.parse_args()

    import torch
    import torch.nn as nn
    from panns_inference import AudioTagging

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading PANN Cnn14 (downloads ~80 MB on first run)")
    tagger = AudioTagging(checkpoint_path=None, device="cpu")
    inner = tagger.model
    inner.eval()

    class Cnn14Trunk(nn.Module):
        """Returns just ``clipwise_output`` so ONNX sees a tensor, not a dict."""

        def __init__(self, model: nn.Module) -> None:
            super().__init__()
            self.model = model

        def forward(self, audio: torch.Tensor) -> torch.Tensor:
            out = self.model(audio, None)
            return out["clipwise_output"]

    trunk = Cnn14Trunk(inner).eval()

    rng = np.random.default_rng(0)
    sample_audio = rng.standard_normal(int(PANN_SR * SAMPLE_LEN_S)).astype(np.float32) * 0.05
    sample_path = args.out_dir / "pann_sample_audio.npy"
    np.save(sample_path, sample_audio)
    print(f"  wrote {sample_path}  shape={sample_audio.shape}")

    dummy = torch.from_numpy(sample_audio[None, :])
    with torch.no_grad():
        torch_out = trunk(dummy).cpu().numpy()
    print(f"  torch reference output shape={torch_out.shape}  dtype={torch_out.dtype}")

    onnx_path = args.out_dir / "pann_cnn14.onnx"
    print(f"Exporting ONNX -> {onnx_path}  (opset={args.opset})")
    torch.onnx.export(
        trunk,
        (dummy,),
        str(onnx_path),
        input_names=["audio"],
        output_names=["clipwise_output"],
        dynamic_axes={
            "audio": {0: "batch", 1: "samples"},
            "clipwise_output": {0: "batch"},
        },
        opset_version=args.opset,
        do_constant_folding=True,
    )

    print("  consolidating external weight data into a single .onnx file")
    _consolidate_external(onnx_path)

    ref_path = args.out_dir / "pann_torch_reference.npy"
    np.save(ref_path, torch_out)
    print(f"  wrote {ref_path} for parity check")

    sha256 = _sha256_file(onnx_path)
    size_bytes = onnx_path.stat().st_size
    print(f"\n  final artifact: {onnx_path}")
    print(f"    sha256: {sha256}")
    print(f"    size:   {size_bytes/1024/1024:.1f} MiB ({size_bytes} bytes)")

    snippet = {
        ARTIFACT_SLUG: {
            "filename": onnx_path.name,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "url": f"{PLACEHOLDER_URL_BASE}/{sha256}/{onnx_path.name}",
        }
    }
    print("\n  model_artifacts snippet -- paste into ensemble_calibration.json:")
    print(json.dumps(snippet, indent=2))
    print(
        "\nDone. Run scripts/verify_onnx_parity.py to compare ONNX vs torch, "
        "or pytest tests/test_onnx_parity.py with SPLITSMITH_ONNX_PANN set."
    )


if __name__ == "__main__":
    main()
