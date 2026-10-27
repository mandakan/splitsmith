#!/usr/bin/env bash
# Cross-compile the Windows ffmpeg with mingw-w64 inside ubuntu:24.04, on
# whatever arch the host is (the compiler targets x86_64 Windows either way).
# Same flags as build-ffmpeg.sh --windows; output lands in desktop/build/.
# Docker writes as root, so build/ is handed back to the caller on exit,
# including a failed run.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
docker run --rm -v "$HERE:/desktop" -w /desktop ubuntu:24.04 bash -c '
  set -euo pipefail
  trap "chown -R '"$(id -u):$(id -g)"' build 2>/dev/null || true" EXIT
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq build-essential nasm pkg-config meson ninja-build autoconf automake libtool \
    mingw-w64 curl xz-utils bzip2 ca-certificates >/dev/null
  ./build-ffmpeg.sh --windows "$@"
' -- "$@"
