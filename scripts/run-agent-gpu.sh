#!/usr/bin/env bash
# Run the native (no-Docker) splitsmith GPU agent (issue #796).
#
# Thin wrapper over `<venv>/bin/splitsmith agent`. GPU acceleration needs no env
# setup -- build_onnx_session preloads the CUDA wheel libraries at runtime and a
# normal WSL2/Linux install already exposes libcuda -- so this only picks the
# venv, sets SPLITSMITH_ONNX_DEVICE (default auto), and execs the agent with
# whatever args you pass through.
#
# Usage:
#   scripts/run-agent-gpu.sh --server-url https://my.splitsmith.app --token <TOKEN>
#   VENV_DIR=/path/to/venv scripts/run-agent-gpu.sh --server-url ...
#
# Env:
#   VENV_DIR               agent venv (default: .venv-agent-gpu, from setup-agent-gpu.sh)
#   SPLITSMITH_ONNX_DEVICE cpu | cuda | auto (default: auto)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv-agent-gpu}"
AGENT="$VENV_DIR/bin/splitsmith"

if [ ! -x "$AGENT" ]; then
    echo "error: $AGENT not found. Run scripts/setup-agent-gpu.sh first." >&2
    exit 1
fi

export SPLITSMITH_ONNX_DEVICE="${SPLITSMITH_ONNX_DEVICE:-auto}"
exec "$AGENT" agent "$@"
