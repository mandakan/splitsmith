#!/usr/bin/env bash
# Download the pinned ffmpeg tarball into desktop/build/bin, verifying sha256.
# The release is ours (built by build-ffmpeg.sh, sources attached), so the
# URL cannot be pruned under us the way third-party autobuilds are.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TAG="ffmpeg-macos-arm64-9.0.2-r1"
ASSET="ffmpeg-macos-arm64-9.0.2-gpl.tar.gz"
SHA256="a7e2482a55deeb430170661766d28c5666accad2e8710794d15a621cb6798e66"
URL="https://github.com/mandakan/splitsmith/releases/download/$TAG/$ASSET"
DEST="$HERE/build/bin"
mkdir -p "$DEST"
tarball="$HERE/build/$ASSET"
if [ -f "$tarball" ] && ! echo "$SHA256  $tarball" | shasum -a 256 -c - >/dev/null 2>&1; then
  rm -f "$tarball"
fi
[ -f "$tarball" ] || curl -fsSL -o "$tarball" "$URL"
echo "$SHA256  $tarball" | shasum -a 256 -c -
tar -xzf "$tarball" -C "$DEST" ffmpeg ffprobe
chmod +x "$DEST/ffmpeg" "$DEST/ffprobe"
"$DEST/ffmpeg" -version | head -1
