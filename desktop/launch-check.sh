#!/usr/bin/env bash
# Start the built AppImage's own Electron + main.js + sidecar under xvfb and
# wait for the sidecar to answer, then shut it down. Runs the extracted
# binary with --no-sandbox and APPIMAGE unset on purpose: CI has no FUSE,
# Docker's seccomp blocks unshare, and the ubuntu-latest host restricts
# user namespaces -- the sandbox refusal would (correctly) trigger in all
# three. sandbox.test.ts and the manual checklist cover the refusal.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="$(ls "$HERE"/dist/*.AppImage | head -1)"
WORK="$(mktemp -d)"
pid=""
# setsid puts xvfb-run, Xvfb, Electron and the sidecar in one process group;
# killing xvfb-run alone leaves Xvfb running.
cleanup() { [ -n "$pid" ] && kill -- -"$pid" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT
(cd "$WORK" && "$IMG" --appimage-extract >/dev/null)
# A fresh HOME keeps the app away from any real ~/.config/splitsmith and its
# hosted token. SPLITSMITH_AUTO_SYNC=0 alone would not: sidecarSpec drops
# every inherited SPLITSMITH_* variable.
export HOME="$WORK/home" XDG_STATE_HOME="$WORK/state" XDG_CACHE_HOME="$WORK/cache" SPLITSMITH_AUTO_SYNC=0
mkdir -p "$HOME"
unset APPIMAGE
setsid xvfb-run -a "$WORK/squashfs-root/splitsmith-desktop" --no-sandbox >"$WORK/electron.log" 2>&1 &
pid=$!
# The main process reads the sidecar's READY banner without echoing it, so
# the URL comes from the sidecar's own log: its startup health probe logs
# "GET http://127.0.0.1:<port>/api/health".
log="$XDG_STATE_HOME/splitsmith/logs"
base=""
for _ in $(seq 1 120); do
  base="$(grep -rhoE 'http://127\.0\.0\.1:[0-9]+' "$log" 2>/dev/null | head -1 || true)"
  [ -n "$base" ] && curl -fsS "$base/api/health" >/dev/null 2>&1 && break
  sleep 1
done
[ -n "$base" ] || { echo "no sidecar URL in $log"; cat "$WORK/electron.log"; exit 1; }
curl -fsS "$base/api/health" | grep -q '"status":"ok"'
echo "launch ok: $IMG ($base)"
