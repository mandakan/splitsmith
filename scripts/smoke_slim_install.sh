#!/usr/bin/env bash
#
# Local mirror of the ``slim-smoke`` CI job (.github/workflows/ci.yml).
# Same release-gating end-to-end check, run from your laptop with no
# GitHub Actions minutes or R2 egress against the CI budget.
#
# Cost on the local side: ~2 min wall + ~440 MiB R2 egress (one-time
# per host -- the cache survives in ``~/.claude-tmp/smoke-cfg/`` so
# re-runs skip the download).
#
# Usage:
#   scripts/smoke_slim_install.sh                  # full smoke
#   scripts/smoke_slim_install.sh --rebuild-venv   # delete the venv first
#   scripts/smoke_slim_install.sh --refetch-models # clear the model cache
#
# Run before opening a PR that touches slim-runtime surface
# (pyproject runtime deps, ensemble/, models/, ui/server.py).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH_ROOT="${SMOKE_SCRATCH_ROOT:-${HOME}/.claude-tmp}"
SMOKE_DIR="${SCRATCH_ROOT}/slim-smoke"
SMOKE_VENV="${SMOKE_DIR}/venv"
SMOKE_CFG="${SMOKE_DIR}/cfg"
SAMPLE_WAV="${REPO_ROOT}/tests/fixtures/stage_sample.wav"

REBUILD_VENV=0
REFETCH_MODELS=0
for arg in "$@"; do
    case "$arg" in
        --rebuild-venv) REBUILD_VENV=1 ;;
        --refetch-models) REFETCH_MODELS=1 ;;
        -h | --help)
            sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown arg: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

cd "$REPO_ROOT"

# --- prerequisites ---------------------------------------------------

require() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "missing prerequisite: $1" >&2
        exit 1
    fi
}
require uv
require ffmpeg
require ffprobe
require pnpm

if [[ ! -f "$SAMPLE_WAV" ]]; then
    echo "missing fixture: $SAMPLE_WAV" >&2
    exit 1
fi

mkdir -p "$SMOKE_DIR"

# --- SPA build + wheel build ----------------------------------------

echo "==> building SPA"
(cd src/splitsmith/ui_static && pnpm install --frozen-lockfile && pnpm build) >/dev/null

echo "==> building wheel"
uv build >/dev/null
WHEEL=$(ls -t dist/splitsmith-*.whl | head -1)
echo "    $WHEEL"

# --- venv setup ------------------------------------------------------

if [[ "$REBUILD_VENV" == 1 ]] && [[ -d "$SMOKE_VENV" ]]; then
    echo "==> rm -rf $SMOKE_VENV"
    rm -rf "$SMOKE_VENV"
fi

if [[ ! -d "$SMOKE_VENV" ]]; then
    echo "==> creating fresh venv at $SMOKE_VENV"
    uv venv "$SMOKE_VENV" --python 3.11 >/dev/null
fi

echo "==> installing wheel"
uv pip install --python "$SMOKE_VENV/bin/python" "$WHEEL" --quiet --reinstall

# --- sentinel: no torch trio ----------------------------------------

echo "==> sentinel: torch / transformers / panns_inference must NOT be installed"
"$SMOKE_VENV/bin/python" - <<'PY'
import sys

forbidden = ("torch", "transformers", "panns_inference")
for name in forbidden:
    try:
        __import__(name)
    except ImportError:
        continue
    raise SystemExit(
        f"slim wheel pulled in {name} -- the [dev] group leaked into the runtime install"
    )

required = ("onnxruntime", "librosa", "huggingface_hub", "numpy", "PIL")
for name in required:
    __import__(name)

print("    import surface ok: no torch trio; slim ML stack present")
PY

# --- CLI sanity ------------------------------------------------------

echo "==> splitsmith --help"
"$SMOKE_VENV/bin/splitsmith" --help >/dev/null

# --- model cache + fetch --------------------------------------------

export SPLITSMITH_CONFIG_DIR="$SMOKE_CFG"
if [[ "$REFETCH_MODELS" == 1 ]] && [[ -d "$SMOKE_CFG/models" ]]; then
    echo "==> rm -rf $SMOKE_CFG/models"
    rm -rf "$SMOKE_CFG/models"
fi

echo "==> fetch-models --list"
"$SMOKE_VENV/bin/splitsmith" fetch-models --list

echo "==> fetch-models (downloads if missing)"
"$SMOKE_VENV/bin/splitsmith" fetch-models

# --- detection smoke -------------------------------------------------

echo "==> splitsmith detect on tests/fixtures/stage_sample.wav"
DETECT_LOG="${SMOKE_DIR}/detect-output.txt"
"$SMOKE_VENV/bin/splitsmith" detect \
    --video "$SAMPLE_WAV" \
    --time 14.74 \
    | tee "$DETECT_LOG"

count=$(grep -oE '[0-9]+ shots' "$DETECT_LOG" | head -1 | grep -oE '[0-9]+' || true)
if [[ -z "$count" ]]; then
    echo "FAIL: detect produced no parseable shot count" >&2
    exit 1
fi
echo "    candidate count: $count"
if (( count < 20 || count > 80 )); then
    echo "FAIL: detect produced $count candidates; expected 20-80" >&2
    exit 1
fi

echo
echo "OK: slim-runtime smoke test passed"
echo "    venv:   $SMOKE_VENV"
echo "    config: $SMOKE_CFG"
echo "    detect: $DETECT_LOG"
