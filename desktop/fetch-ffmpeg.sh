#!/usr/bin/env bash
# Download the pinned ffmpeg tarball into desktop/build/bin, verifying sha256.
#   desktop/fetch-ffmpeg.sh                     the build host's target
#   desktop/fetch-ffmpeg.sh --target linux-x86_64
# The release is ours (built by build-ffmpeg.sh, sources attached), so the
# URL cannot be pruned under us the way third-party autobuilds are.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/target.sh
source "$HERE/lib/target.sh"
TARGET="$(host_target)"
[ "${1:-}" = --target ] && TARGET="$2"
case "$TARGET" in
  macos-aarch64)
    TAG="ffmpeg-macos-arm64-9.0.2-r1"
    ASSET="ffmpeg-macos-arm64-9.0.2-gpl.tar.gz"
    SHA256="a7e2482a55deeb430170661766d28c5666accad2e8710794d15a621cb6798e66" ;;
  linux-x86_64)
    TAG="ffmpeg-linux-x86_64-9.0.2-r1"
    ASSET="ffmpeg-linux-x86_64-9.0.2-gpl.tar.gz"
    SHA256="3c8a08e7a77b840e1cd7006d86cc4c49cd988daf9f492d7fcd4ac1393d3a43a3" ;;
  *) echo "unknown target $TARGET" >&2; exit 2 ;;
esac
URL="https://github.com/mandakan/splitsmith/releases/download/$TAG/$ASSET"
DEST="$HERE/build/bin"
mkdir -p "$DEST"
tarball="$HERE/build/$ASSET"
if [ -f "$tarball" ] && ! sha256_verify "$SHA256" "$tarball" 2>/dev/null; then
  rm -f "$tarball"
fi
[ -f "$tarball" ] || curl -fsSL -o "$tarball" "$URL"
sha256_verify "$SHA256" "$tarball"
tar -xzf "$tarball" -C "$DEST" ffmpeg ffprobe
# Beside bin/, not in it: electron-builder ships all of build/bin.
echo "$TAG" > "$HERE/build/FFMPEG_RELEASE"
chmod +x "$DEST/ffmpeg" "$DEST/ffprobe"
"$DEST/ffmpeg" -version | head -1
