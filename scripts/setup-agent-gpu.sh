#!/usr/bin/env bash
# Set up a native (no-Docker) splitsmith GPU agent, e.g. on WSL2 (issue #796).
#
# Installs the agent into a dedicated venv and swaps the CPU onnxruntime wheel
# for onnxruntime-gpu + the CUDA 12 / cuDNN 9 runtime wheels, so ensemble detect
# runs on the GPU. NVENC audit encoding needs nothing extra -- it uses the system
# ffmpeg, which the agent probes at runtime.
#
# No LD_LIBRARY_PATH juggling: build_onnx_session calls onnxruntime.preload_dlls(),
# which loads the CUDA libraries straight from the nvidia-*-cu12 wheels. The only
# system requirement is libcuda from the GPU driver, which a normal WSL2/Linux
# install already exposes via /etc/ld.so.conf.d.
#
# Usage:
#   scripts/setup-agent-gpu.sh [VENV_DIR]     # default: .venv-agent-gpu
#
# Then run the agent (see scripts/run-agent-gpu.sh or docs/self-hosted-workers.md):
#   VENV_DIR/bin/splitsmith agent --server-url https://my.splitsmith.app --token <TOKEN>
#
# Pinned to the versions validated on an RTX 2070 SUPER (driver 566.x, CUDA 12).
# A newer onnxruntime-gpu targets CUDA 13 and needs a 580+ driver -- do not bump
# these without re-checking the driver/CUDA matrix and re-running the parity check.
set -euo pipefail

ORT_GPU_VERSION="1.22.0"
VENV_DIR="${1:-.venv-agent-gpu}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi

echo "==> Creating venv at $VENV_DIR (Python 3.11)"
uv venv "$VENV_DIR" --python 3.11

echo "==> Installing splitsmith + hosted deps (this is the agent's runtime)"
uv pip install --python "$VENV_DIR" -e ".[hosted]"

echo "==> Swapping CPU onnxruntime -> onnxruntime-gpu $ORT_GPU_VERSION + CUDA 12 wheels"
# onnxruntime and onnxruntime-gpu share the 'onnxruntime' import name (and the
# same on-disk dir) and cannot coexist. A base (re)install lays the CPU wheel
# down over the GPU files; uninstalling then installing by-name is a no-op when
# the GPU dist-info survives, leaving a gutted namespace package (no
# __init__.py, `import onnxruntime` -> __file__=None, no CUDA). Re-running this
# script or upgrading the base package hits exactly that. So remove BOTH dists
# and any leftover dir, then reinstall the GPU wheel fresh (--reinstall-package)
# so the module is whole every time.
uv pip uninstall --python "$VENV_DIR" onnxruntime onnxruntime-gpu >/dev/null 2>&1 || true
_sp="$("$VENV_DIR/bin/python" -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null)"
[ -n "$_sp" ] && rm -rf "$_sp/onnxruntime"
uv pip install --python "$VENV_DIR" --reinstall-package onnxruntime-gpu \
    "onnxruntime-gpu==${ORT_GPU_VERSION}" \
    nvidia-cudnn-cu12 \
    nvidia-cublas-cu12 \
    nvidia-cuda-runtime-cu12 \
    nvidia-curand-cu12 \
    nvidia-cufft-cu12

echo "==> Verifying the CUDA execution provider initialises"
"$VENV_DIR/bin/python" - <<'PY'
import sys
import onnxruntime as ort

ort.preload_dlls()
available = ort.get_available_providers()
if "CUDAExecutionProvider" not in available:
    sys.exit(f"CUDAExecutionProvider not built into onnxruntime-gpu: {available}")

# A real session on a shipped graph is the honest test -- the provider is only
# usable if a session actually binds to it.
from pathlib import Path

model = Path("src/splitsmith/data/voter_c_gbdt_headcam.onnx")
sess = ort.InferenceSession(str(model), providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
if "CUDAExecutionProvider" not in sess.get_providers():
    sys.exit(
        "onnxruntime-gpu is installed but the session fell back to CPU. Check the "
        "GPU driver (nvidia-smi should report CUDA >= 12.4) and that libcuda is on "
        "the loader path (/usr/lib/wsl/lib on WSL2)."
    )
print("OK: ensemble detect will run on", sess.get_providers()[0])
PY

echo "==> Verifying ffmpeg NVENC (for audit-mode encode)"
if "$VENV_DIR/bin/python" - <<'PY'
import subprocess
from splitsmith.trim import select_audit_encoder

enc = select_audit_encoder("auto")
print(f"audit encoder: {enc}")
raise SystemExit(0 if enc == "h264_nvenc" else 1)
PY
then
    echo "    NVENC available."
else
    echo "    NVENC not usable here (audit encodes will use libx264). GPU detect still active."
fi

cat <<EOF

Done. Run the agent with:

  $VENV_DIR/bin/splitsmith agent \\
    --server-url https://my.splitsmith.app \\
    --token <REGISTRATION_TOKEN>

or:  scripts/run-agent-gpu.sh --server-url ... --token ...
EOF
