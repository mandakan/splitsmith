#!/usr/bin/env bash
# Exercise the built app's sidecar the way the shell does: launch it from
# Contents/Resources with the bundle's ffmpeg, check health and the
# encoder, run a detection through the bundled CLI, shut down through the
# route. Needs network the first time unless a model cache is reused.
set -euo pipefail
APP="${1:-$(cd "$(dirname "$0")" && pwd)/dist/mac-arm64/Splitsmith.app}"
RES="$APP/Contents/Resources"
PY="$RES/python/bin/python3.12"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$(mktemp -d)"
MODELS="${SPLITSMITH_SMOKE_MODELS:-$HOME/.splitsmith/models}"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; rm -rf "$CFG"; }
trap cleanup EXIT

[ -x "$PY" ] || { echo "no interpreter at $PY" >&2; exit 1; }
[ -x "$RES/bin/ffmpeg" ] || { echo "no ffmpeg in bundle" >&2; exit 1; }
# Reuse a model cache when one is available so the smoke run is offline.
if [ -d "$MODELS" ]; then ln -s "$MODELS" "$CFG/models"; fi

export SPLITSMITH_CONFIG_DIR="$CFG" SPLITSMITH_PORT=0 SPLITSMITH_HOST=127.0.0.1
export SPLITSMITH_FFMPEG="$RES/bin/ffmpeg" SPLITSMITH_FFPROBE="$RES/bin/ffprobe"
export NUMBA_CACHE_DIR="$CFG/numba" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
unset PYTHONPATH PYTHONHOME

"$PY" -m splitsmith.ui.embedded --log-dir "$CFG/logs" 2> "$CFG/stderr" &
pid=$!
for _ in $(seq 1 120); do grep -q SPLITSMITH_READY "$CFG/stderr" && break; sleep 0.5; done
banner="$(grep SPLITSMITH_READY "$CFG/stderr" | head -1 | cut -d' ' -f2-)"
[ -n "$banner" ] || { echo "no READY banner"; cat "$CFG/stderr"; exit 1; }
base="$(printf '%s' "$banner" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["base_url"])')"
ffm="$(printf '%s' "$banner" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["ffmpeg_binary"])')"
[ "$ffm" = "$RES/bin/ffmpeg" ] || { echo "sidecar resolved ffmpeg to $ffm, not the bundle"; exit 1; }

curl -fsS "$base/api/health" | grep -q '"status":"ok"'
encoders="$("$RES/bin/ffmpeg" -hide_banner -encoders)"
grep -q h264_videotoolbox <<<"$encoders" || { echo "bundled ffmpeg lacks h264_videotoolbox"; exit 1; }

# Detection through the bundled CLI (same fixture and range as ci.yml's slim-smoke).
"$RES/python/bin/splitsmith" detect \
  --video "$ROOT/tests/fixtures/stage-shots-tallmilan-2026-stage3-s97dcec94.wav" --time 14.74 | tee "$CFG/detect.txt"
count="$(grep -oE '[0-9]+ shots' "$CFG/detect.txt" | head -1 | grep -oE '[0-9]+' || true)"
if [ -z "$count" ] || [ "$count" -lt 20 ] || [ "$count" -gt 80 ]; then
  echo "detect produced '$count' candidates; expected 20-80"; exit 1
fi

curl -fsS -X POST "$base/api/shutdown" >/dev/null
for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
if kill -0 "$pid" 2>/dev/null; then echo "sidecar did not exit after /api/shutdown"; exit 1; fi
wait "$pid" || true
pid=""
echo "smoke ok: $APP"
