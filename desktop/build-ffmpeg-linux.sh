#!/usr/bin/env bash
# Build the Linux ffmpeg inside ubuntu:22.04 (glibc 2.35, the app's floor).
# Same flags as build-ffmpeg.sh; output lands in desktop/build/ as on macOS.
# Docker writes as root, so build/ is handed back to the caller on exit,
# including a failed run.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
docker run --rm -v "$HERE:/desktop" -w /desktop ubuntu:22.04 bash -c '
  set -euo pipefail
  trap "chown -R '"$(id -u):$(id -g)"' build 2>/dev/null || true" EXIT
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq build-essential nasm pkg-config meson ninja-build autoconf automake libtool \
    curl xz-utils bzip2 zlib1g-dev libbz2-dev ca-certificates >/dev/null
  ./build-ffmpeg.sh "$@"
' -- "$@"
