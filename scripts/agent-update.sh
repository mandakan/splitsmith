#!/usr/bin/env bash
# Upgrade a native (PyPI-venv) splitsmith agent in place and restart it when idle.
#
# Meant to run as root from the splitsmith-agent-update.timer in scripts/systemd/;
# root is what lets it read the agent's journal (the idle gate) and restart the
# unit. Everything that touches the venv runs as AGENT_USER through runuser, so
# the venv stays owned by the user who registered the worker.
#
# One tick does, in order:
#   1. Compare the venv's installed version with the latest on PyPI. If they
#      differ: `uv pip install -U "splitsmith[hosted]"`, then (on a GPU venv) redo
#      the onnxruntime -> onnxruntime-gpu swap from setup-agent-gpu.sh, because
#      the base upgrade lays the CPU wheel over the GPU files, and verify the
#      CUDA provider binds. A failed verify leaves the agent running the old code
#      (no restart is queued) and the next tick retries the swap.
#   2. If an upgrade is pending a restart (stamp file in STATE_DIR) and the
#      agent's last drain marker says it is idle, restart the unit.
#
# Steps are decoupled through the stamp so a tick that upgrades while the agent
# is busy doesn't have to choose between killing a job and never restarting.
#
# Env (all optional):
#   AGENT_USER        user who owns the venv and state dir (default: the unit's User=)
#   VENV              agent venv (default: ~AGENT_USER/.venv-splitsmith-agent)
#   STATE_DIR         agent state dir (default: ~AGENT_USER/.splitsmith)
#   SERVICE           systemd unit to restart (default: splitsmith-agent)
#   GPU               1 | 0 | auto (default auto: GPU if onnxruntime-gpu is installed)
#   ORT_GPU_VERSION   onnxruntime-gpu pin (default 1.22.0, mirrors setup-agent-gpu.sh)
#   DRY_RUN=1         print what would change; install nothing, restart nothing
set -euo pipefail

SERVICE="${SERVICE:-splitsmith-agent}"
ORT_GPU_VERSION="${ORT_GPU_VERSION:-1.22.0}"
GPU="${GPU:-auto}"
DRY_RUN="${DRY_RUN:-0}"

if [ -z "${AGENT_USER:-}" ]; then
    AGENT_USER="$(systemctl show -p User --value "$SERVICE" 2>/dev/null || true)"
fi
if [ -z "$AGENT_USER" ]; then
    echo "error: AGENT_USER not set and $SERVICE has no User=" >&2
    exit 1
fi
AGENT_HOME="$(getent passwd "$AGENT_USER" | cut -d: -f6)"
VENV="${VENV:-$AGENT_HOME/.venv-splitsmith-agent}"
STATE_DIR="${STATE_DIR:-$AGENT_HOME/.splitsmith}"
STAMP="$STATE_DIR/.restart-pending"
PY="$VENV/bin/python"

as_user() {
    if [ "$(id -un)" = "$AGENT_USER" ]; then
        "$@"
    else
        runuser -u "$AGENT_USER" -- "$@"
    fi
}

uv_bin() {
    # uv is normally a per-user install; look in the agent user's PATH first.
    local cand="$AGENT_HOME/.local/bin/uv"
    if [ -x "$cand" ]; then echo "$cand"; return; fi
    command -v uv || { echo "error: uv not found for $AGENT_USER" >&2; exit 1; }
}

if [ ! -x "$PY" ]; then
    echo "error: $PY not found -- is $VENV the agent venv?" >&2
    exit 1
fi

installed="$(as_user "$PY" -c 'import importlib.metadata as m; print(m.version("splitsmith"))')"
latest="$(curl -fsS --max-time 30 https://pypi.org/pypi/splitsmith/json \
    | as_user "$PY" -c 'import json, sys; print(json.load(sys.stdin)["info"]["version"])')"

want_gpu() {
    case "$GPU" in
        1) return 0 ;;
        0) return 1 ;;
        *) as_user "$PY" -c 'import importlib.metadata as m; m.version("onnxruntime-gpu")' >/dev/null 2>&1 ;;
    esac
}

cuda_ok() {
    as_user "$PY" - <<'PY' >/dev/null 2>&1
import sys
import onnxruntime as ort

ort.preload_dlls()
if "CUDAExecutionProvider" not in ort.get_available_providers():
    sys.exit(1)
from importlib.resources import files

model = files("splitsmith.data") / "voter_c_gbdt_headcam.onnx"
sess = ort.InferenceSession(str(model), providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
sys.exit(0 if "CUDAExecutionProvider" in sess.get_providers() else 1)
PY
}

gpu_swap() {
    # Same recipe as setup-agent-gpu.sh: remove BOTH dists and the leftover dir,
    # then force-reinstall the GPU wheel so the package is whole.
    local uv sp
    uv="$(uv_bin)"
    as_user "$uv" pip uninstall --python "$VENV" onnxruntime onnxruntime-gpu >/dev/null 2>&1 || true
    sp="$(as_user "$PY" -c 'import site; print(site.getsitepackages()[0])')"
    [ -n "$sp" ] && as_user rm -rf "$sp/onnxruntime"
    as_user "$uv" pip install --python "$VENV" --reinstall-package onnxruntime-gpu \
        "onnxruntime-gpu==${ORT_GPU_VERSION}" \
        nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 \
        nvidia-curand-cu12 nvidia-cufft-cu12
}

# Decide GPU-ness BEFORE upgrading: the upgrade removes onnxruntime-gpu's
# dist-info, so `auto` would read as CPU afterwards.
gpu=0
want_gpu && gpu=1

if [ "$installed" != "$latest" ]; then
    echo "==> splitsmith $installed installed, $latest on PyPI"
    if [ "$DRY_RUN" = 1 ]; then
        echo "    (dry run) would upgrade, gpu=$gpu"
    else
        as_user "$(uv_bin)" pip install --python "$VENV" -U "splitsmith[hosted]"
        as_user touch "$STAMP"
    fi
else
    echo "==> splitsmith $installed is current"
fi

if [ "$gpu" = 1 ] && [ "$DRY_RUN" != 1 ]; then
    if cuda_ok; then
        echo "==> CUDAExecutionProvider OK"
    else
        echo "==> CUDAExecutionProvider missing; redoing the onnxruntime-gpu swap"
        gpu_swap
        if cuda_ok; then
            echo "==> CUDAExecutionProvider OK after swap"
            as_user touch "$STAMP"
        else
            echo "error: onnxruntime-gpu swap did not restore CUDA; not restarting $SERVICE" >&2
            exit 1
        fi
    fi
fi

# Restart gate. The agent logs "wake received; draining" when a drain starts and
# "drain finished; waiting" (or "drain finished; stopping", when a SIGTERM
# arrived mid-drain) when it ends; whichever came last is the state. Match both
# end markers: a drain stopped by a restart must not read as busy afterwards.
# No marker at all (fresh boot, never woken) counts as idle.
agent_idle() {
    local last
    last="$(journalctl -u "$SERVICE" -b --no-pager -o cat 2>/dev/null \
        | grep -E 'wake received; draining|drain finished; (waiting|stopping)' | tail -n 1 || true)"
    [[ "$last" != *"wake received; draining"* ]]
}

if [ -e "$STAMP" ]; then
    if agent_idle; then
        if [ "$DRY_RUN" = 1 ]; then
            echo "==> (dry run) would restart $SERVICE"
        else
            echo "==> restarting $SERVICE"
            systemctl restart "$SERVICE"
            rm -f "$STAMP"
        fi
    else
        echo "==> $SERVICE is draining; restart deferred to a later tick"
    fi
elif [ "$DRY_RUN" = 1 ] && [ "$installed" != "$latest" ]; then
    if agent_idle; then echo "    (dry run) agent idle, would restart after upgrade"; else echo "    (dry run) agent busy, restart would be deferred"; fi
fi
