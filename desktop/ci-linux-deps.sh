#!/usr/bin/env bash
# Packages the ubuntu:22.04 build container needs for desktop/build.sh --linux,
# smoke.sh and launch-check.sh. Node/pnpm/uv come from their setup actions.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates xz-utils file dpkg-dev \
  xvfb xauth libnss3 libgbm1 libasound2 libgtk-3-0 libxss1 libnotify4 libatspi2.0-0 libdrm2 libxkbcommon0 \
  >/dev/null
