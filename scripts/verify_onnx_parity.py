"""Compare CLAP / PANN ONNX outputs against their torch references.

Reads the artifacts written by ``export_clap_onnx.py`` and
``export_pann_onnx.py`` from ``build/onnx-spike/`` and checks that
``onnxruntime`` produces outputs matching the torch reference within
tolerance. Tolerance defaults are deliberately tight (1e-4 absolute,
1e-3 relative); loosen with ``--atol`` / ``--rtol`` if the platform
introduces rounding drift.

Exit code is non-zero if any check fails so this can run in CI once the
full slim path lands.

Run:
    uv pip install onnxruntime
    uv run python scripts/verify_onnx_parity.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

OUT_DIR = Path("build/onnx-spike")


def _check(name: str, ref: np.ndarray, got: np.ndarray, atol: float, rtol: float) -> bool:
    if ref.shape != got.shape:
        print(f"  FAIL {name}: shape mismatch  ref={ref.shape}  got={got.shape}")
        return False
    diff = np.abs(ref - got)
    max_abs = float(diff.max())
    max_rel = float((diff / (np.abs(ref) + 1e-9)).max())
    ok = np.allclose(ref, got, atol=atol, rtol=rtol)
    flag = "PASS" if ok else "FAIL"
    print(f"  {flag} {name}: max_abs={max_abs:.3e}  max_rel={max_rel:.3e}")
    return ok


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out-dir", type=Path, default=OUT_DIR)
    p.add_argument("--atol", type=float, default=1e-4)
    p.add_argument("--rtol", type=float, default=1e-3)
    args = p.parse_args()

    import onnxruntime as ort

    all_ok = True

    clap_onnx = args.out_dir / "clap_audio.onnx"
    if clap_onnx.exists():
        print(f"CLAP: {clap_onnx}")
        sample = np.load(args.out_dir / "clap_sample_input_features.npy")
        ref = np.load(args.out_dir / "clap_torch_reference.npy")
        sess = ort.InferenceSession(str(clap_onnx), providers=["CPUExecutionProvider"])
        (got,) = sess.run(None, {"input_features": sample})
        all_ok &= _check("clap_audio", ref, got, args.atol, args.rtol)
    else:
        print(f"CLAP: skip (missing {clap_onnx}; run export_clap_onnx.py first)")

    pann_onnx = args.out_dir / "pann_cnn14.onnx"
    if pann_onnx.exists():
        print(f"\nPANN: {pann_onnx}")
        sample = np.load(args.out_dir / "pann_sample_audio.npy")[None, :]
        ref = np.load(args.out_dir / "pann_torch_reference.npy")
        sess = ort.InferenceSession(str(pann_onnx), providers=["CPUExecutionProvider"])
        (got,) = sess.run(None, {"audio": sample})
        all_ok &= _check("pann_clipwise", ref, got, args.atol, args.rtol)

        gunshot_idx = 427
        ref_g = ref[:, gunshot_idx]
        got_g = got[:, gunshot_idx]
        all_ok &= _check("pann_gunshot_class", ref_g, got_g, args.atol, args.rtol)
    else:
        print(f"\nPANN: skip (missing {pann_onnx}; run export_pann_onnx.py first)")

    print()
    if all_ok:
        print("All parity checks passed.")
        sys.exit(0)
    else:
        print("Parity check FAILED. See diffs above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
