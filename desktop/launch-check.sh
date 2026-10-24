#!/usr/bin/env bash
# Start the built app's own Electron + main.js + sidecar and wait for the
# sidecar to answer, then shut it down.
#   desktop/launch-check.sh              -> dist/'s AppImage, or on a Mac dist/mac-arm64/Splitsmith.app
#   desktop/launch-check.sh APP.app      -> that app (a Mac; e.g. one check-dmg.sh installed)
#
# Linux runs the extracted AppImage under xvfb with --no-sandbox and APPIMAGE
# unset on purpose: CI has no FUSE, Docker's seccomp blocks unshare, and the
# ubuntu-latest host restricts user namespaces -- the sandbox refusal would
# (correctly) trigger in all three. sandbox.test.ts and the manual checklist
# cover the refusal. A Mac runs the bundle's executable directly and also
# checks that quitting Electron takes the sidecar with it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
pid=""
mac=0
[ "$(uname -s)" = Darwin ] && mac=1
cleanup() {
  if [ -n "$pid" ]; then
    # setsid put the Linux tree in one process group; killing xvfb-run alone
    # leaves Xvfb running.
    if [ "$mac" = 1 ]; then kill "$pid" 2>/dev/null || true; else kill -- -"$pid" 2>/dev/null || true; fi
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
# A fresh HOME keeps the app away from any real ~/.splitsmith, its logs and
# its hosted token. SPLITSMITH_AUTO_SYNC=0 alone would not: sidecarSpec drops
# every inherited SPLITSMITH_* variable.
export HOME="$WORK/home" SPLITSMITH_AUTO_SYNC=0
mkdir -p "$HOME"
if [ "$mac" = 1 ]; then
  APP="${1:-$HERE/dist/mac-arm64/Splitsmith.app}"
  exe="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
  "$exe" >"$WORK/electron.log" 2>&1 &
  pid=$!
  log="$HOME/Library/Logs/Splitsmith"
  what="$APP"
else
  IMG="$(ls "$HERE"/dist/*.AppImage | head -1)"
  (cd "$WORK" && "$IMG" --appimage-extract >/dev/null)
  export XDG_STATE_HOME="$WORK/state" XDG_CACHE_HOME="$WORK/cache"
  unset APPIMAGE
  setsid xvfb-run -a "$WORK/squashfs-root/splitsmith-desktop" --no-sandbox >"$WORK/electron.log" 2>&1 &
  pid=$!
  log="$XDG_STATE_HOME/splitsmith/logs"
  what="$IMG"
fi
# The main process reads the sidecar's READY banner without echoing it, so
# the URL comes from the sidecar's own log: its startup health probe logs
# "GET http://127.0.0.1:<port>/api/health".
base=""
for _ in $(seq 1 120); do
  base="$(grep -rhoE 'http://127\.0\.0\.1:[0-9]+' "$log" 2>/dev/null | head -1 || true)"
  [ -n "$base" ] && curl -fsS "$base/api/health" >/dev/null 2>&1 && break
  kill -0 "$pid" 2>/dev/null || { echo "the app exited before the sidecar answered"; cat "$WORK/electron.log"; exit 1; }
  sleep 1
done
[ -n "$base" ] || { echo "no sidecar URL in $log"; cat "$WORK/electron.log"; exit 1; }
curl -fsS "$base/api/health" | grep -q '"status":"ok"'

if [ "$mac" = 1 ]; then
  # SIGTERM is a quit: before-quit stops the sidecar, then Electron exits.
  # Either one still running afterwards is an orphan on a user's Mac.
  kill "$pid"
  for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  if kill -0 "$pid" 2>/dev/null; then echo "Electron did not quit on SIGTERM"; exit 1; fi
  pid=""
  for _ in $(seq 1 10); do curl -fsS "$base/api/health" >/dev/null 2>&1 || break; sleep 1; done
  if curl -fsS "$base/api/health" >/dev/null 2>&1; then echo "the sidecar outlived the app at $base"; exit 1; fi
fi
echo "launch ok: $what ($base)"
