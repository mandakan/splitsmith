#!/usr/bin/env bash
# Exercise the built app's sidecar the way the shell does: launch it from
# Contents/Resources with the bundle's ffmpeg, check health and the
# encoder, run a detection through the bundled CLI, shut down through the
# route. Needs network the first time unless a model cache is reused.
#   desktop/smoke.sh [APP]   APP is an .app, an unpacked app dir with
#                            resources/, or a resources dir itself (a bare
#                            runtime with python/ and bin/ beside it)
# Windows runs it under Git Bash: paths handed to the engine are converted
# to C:\... with cygpath, as the Electron shell would pass them.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-}"
if [ -z "$APP" ]; then
  case "$(uname -s)" in
    Darwin) APP="$HERE/dist/mac-arm64/Splitsmith.app" ;;
    MINGW* | MSYS*) APP="$HERE/dist/win-unpacked" ;;
    *) APP="$HERE/dist/linux-unpacked" ;;
  esac
fi
if [ -d "$APP/Contents/Resources" ]; then RES="$APP/Contents/Resources"
elif [ -d "$APP/python" ]; then RES="$APP"
else RES="$APP/resources"; fi
win=0
command -v cygpath >/dev/null && win=1
# A path as the engine sees it: C:\... on Windows, unchanged elsewhere.
native() { if [ "$win" = 1 ]; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }
if [ "$win" = 1 ]; then
  PY="$RES/python/python.exe"; CLI="$RES/python/Scripts/splitsmith.cmd"; EXE=.exe
else
  PY="$RES/python/bin/python3.12"; CLI="$RES/python/bin/splitsmith"; EXE=""
fi
FFMPEG="$RES/bin/ffmpeg$EXE"
FFPROBE="$RES/bin/ffprobe$EXE"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$(mktemp -d)"
MODELS="${SPLITSMITH_SMOKE_MODELS:-$HOME/.splitsmith/models}"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; rm -rf "$CFG"; }
trap cleanup EXIT

[ -x "$PY" ] || { echo "no interpreter at $PY" >&2; exit 1; }
[ -x "$FFMPEG" ] || { echo "no ffmpeg in bundle" >&2; exit 1; }
# Reuse a model cache when one is available so the smoke run is offline
# (Git Bash's ln -s copies, which is fine for a cache).
if [ -d "$MODELS" ]; then ln -s "$MODELS" "$CFG/models"; fi

export SPLITSMITH_CONFIG_DIR="$(native "$CFG")" SPLITSMITH_PORT=0 SPLITSMITH_HOST=127.0.0.1
export SPLITSMITH_FFMPEG="$(native "$FFMPEG")" SPLITSMITH_FFPROBE="$(native "$FFPROBE")"
export NUMBA_CACHE_DIR="$(native "$CFG/numba")" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
unset PYTHONPATH PYTHONHOME
# The PATH a Mac started from the Finder has: no Homebrew, no ffmpeg. Every
# producer must reach the bundle through the runtime (the 0.81.0 DMG could
# not export a trim because the trimmer asked PATH for a bare "ffmpeg").
# Windows keeps Git Bash's own tools (grep, curl) and the system dirs.
if [ "$win" = 1 ]; then
  sysroot="$(cygpath -u "${SYSTEMROOT:-C:\Windows}")"
  export PATH="/usr/bin:/mingw64/bin:$sysroot/System32:$sysroot"
else
  export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
fi

"$PY" -m splitsmith.ui.embedded --log-dir "$(native "$CFG/logs")" 2> "$CFG/stderr" &
pid=$!
for _ in $(seq 1 120); do grep -q SPLITSMITH_READY "$CFG/stderr" && break; sleep 0.5; done
banner="$(grep SPLITSMITH_READY "$CFG/stderr" | head -1 | cut -d' ' -f2-)"
[ -n "$banner" ] || { echo "no READY banner"; cat "$CFG/stderr"; exit 1; }
base="$(printf '%s' "$banner" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["base_url"])')"
ffm="$(printf '%s' "$banner" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["ffmpeg_binary"])')"
# Python's stdout on Windows ends lines with \r\n; $(...) strips only the \n.
base="${base%$'\r'}" ffm="${ffm%$'\r'}"
[ "$ffm" = "$(native "$FFMPEG")" ] || { echo "sidecar resolved ffmpeg to $ffm, not the bundle"; exit 1; }

curl -fsS "$base/api/health" | grep -q '"status":"ok"'
encoders="$("$FFMPEG" -hide_banner -encoders)"
filters="$("$FFMPEG" -hide_banner -filters)"
if [ "$(uname -s)" = Darwin ]; then
  grep -q h264_videotoolbox <<<"$encoders" || { echo "bundled ffmpeg lacks h264_videotoolbox"; exit 1; }
else
  grep -q libx264 <<<"$encoders" || { echo "bundled ffmpeg lacks libx264"; exit 1; }
  grep -q drawtext <<<"$filters" || { echo "bundled ffmpeg lacks drawtext"; exit 1; }
fi

# A trim and an ffprobe through the producers' defaults, with no ffmpeg on
# PATH: both must run the bundle. The clip is cut by the bundled ffmpeg.
"$FFMPEG" -hide_banner -loglevel error -y -f lavfi -i "testsrc=size=160x90:rate=25:duration=3" \
  -f lavfi -i "sine=frequency=440:duration=3" -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$(native "$CFG/clip.mp4")"
"$PY" - "$(native "$CFG/clip.mp4")" "$(native "$CFG/trim.mp4")" <<'PYEOF'
import sys
from pathlib import Path
from splitsmith import fcpxml_gen, trim
src, out = Path(sys.argv[1]), Path(sys.argv[2])
trim.trim_video(src, out, beep_time=1.0, stage_time=1.0, buffer_seconds=0.5, overwrite=True)
meta = fcpxml_gen.probe_video(out)
assert meta.width == 160, meta
print("trim + probe through the bundle ok")
PYEOF
# Detection through the bundled CLI (same fixture and range as ci.yml's slim-smoke).
"$CLI" detect \
  --video "$(native "$ROOT/tests/fixtures/stage-shots-tallmilan-2026-stage3-s97dcec94-pin.wav")" --time 14.74 | tee "$CFG/detect.txt"
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
