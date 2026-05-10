"""Export the PANN Cnn14 audio tagger to ONNX.

Spike output for issue: drop torch from the production runtime. This
script runs offline against the standard PANN checkpoint
(``~/panns_data/Cnn14_mAP=0.431.pth``, downloaded by
``panns_inference`` on first use) and writes two artifacts under
``build/onnx-spike/``:

* ``pann_cnn14.onnx`` -- takes raw audio at 32 kHz, shape ``(B, T)``,
  outputs ``clipwise_output`` of shape ``(B, 527)``. The mel-spectrogram
  layer is baked into the graph via ``torchlibrosa``; no preprocessing
  needed at runtime beyond resampling.
* ``pann_sample_audio.npy`` and ``pann_torch_reference.npy`` -- a fixed
  random-seed 1 s @ 32 kHz buffer and the torch reference output for
  parity checking.

Known risk: ``torchlibrosa.stft.Spectrogram`` uses ``torch.stft``, which
returns a complex tensor in newer PyTorch. Some ``onnxruntime`` versions
choke on complex ops. If export fails here, fallback is to do the
mel-spec in numpy/librosa and export only the CNN trunk taking
``(B, 1, time_steps, mel_bins)`` log-mel input.

Run:
    uv pip install onnx
    uv run python scripts/export_pann_onnx.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from splitsmith.ensemble.features import PANN_SR

OUT_DIR = Path("build/onnx-spike")
ONNX_OPSET = 17
SAMPLE_LEN_S = 1.0


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

    ref_path = args.out_dir / "pann_torch_reference.npy"
    np.save(ref_path, torch_out)
    print(f"  wrote {ref_path} for parity check")
    print("\nDone. Run scripts/verify_onnx_parity.py to compare ONNX vs torch.")


if __name__ == "__main__":
    main()
