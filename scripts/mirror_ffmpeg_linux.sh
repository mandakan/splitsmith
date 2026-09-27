#!/usr/bin/env bash
# Mirror a BtbN/FFmpeg-Builds autobuild into a release on this repo and point
# Dockerfile + Dockerfile.gpu at it.
#
# BtbN prunes autobuild releases after a couple of weeks (the 2026-09-10 pin
# was gone after 17 days), and every image build 404s the moment the pinned
# tag disappears. A release on this repo is never pruned, so the pin only
# moves when we choose to move it.
#
#   scripts/mirror_ffmpeg_linux.sh autobuild-2026-09-26-13-03
#
# Each tarball is checked against the digest GitHub reports for BtbN's asset
# before it is uploaded, and the Dockerfiles keep verifying that same sha256
# at build time. Moving ffmpeg is a pipeline change: render the frames
# (scripts/render_match_frames.py, scripts/render_grid_frames.py) against the
# old and new binaries before merging the bump.
set -euo pipefail

btbn_tag="${1:?usage: $0 <btbn autobuild tag, e.g. autobuild-2026-09-26-13-03>}"
repo="mandakan/splitsmith"
root="$(cd "$(dirname "$0")/.." && pwd)"
# ffmpeg-linux-<date part of the BtbN tag>. Deliberately not v*.*.*: the
# desktop update feed (functions/desktop/latest.json.js) only considers app
# tags, and the release is created with --latest=false.
our_tag="ffmpeg-linux-${btbn_tag#autobuild-}"

work="$(mktemp -d "${TMPDIR:-/tmp}/ffmpeg-mirror.XXXXXX")"
trap 'rm -rf "$work"' EXIT

assets=$(gh api "repos/BtbN/FFmpeg-Builds/releases/tags/$btbn_tag" \
  --jq '.assets[] | select(.name|test("linux(64|arm64)-gpl-shared\\.tar\\.xz$")) | "\(.name) \(.digest)"')
[ "$(printf '%s\n' "$assets" | grep -c .)" = 2 ] || { echo "expected 2 linux assets in $btbn_tag, got:" >&2; echo "$assets" >&2; exit 1; }

# Plain variables, not an associative array: macOS ships bash 3.2.
amd64_file="" amd64_sha="" arm64_file="" arm64_sha=""
while read -r name digest; do
  case "$name" in
    *-linux64-*) amd64_file="$name" amd64_sha="${digest#sha256:}" ;;
    *-linuxarm64-*) arm64_file="$name" arm64_sha="${digest#sha256:}" ;;
    *) echo "unexpected asset $name" >&2; exit 1 ;;
  esac
done <<< "$assets"
for pair in "$amd64_file $amd64_sha" "$arm64_file $arm64_sha"; do
  set -- $pair
  curl -fsSL --retry 5 --retry-all-errors -o "$work/$1" \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/$btbn_tag/$1"
  echo "$2  $work/$1" | shasum -a 256 -c -
done

if gh release view "$our_tag" -R "$repo" >/dev/null 2>&1; then
  echo "release $our_tag exists; uploading any missing assets"
  for f in "$amd64_file" "$arm64_file"; do
    gh release view "$our_tag" -R "$repo" --json assets --jq '.assets[].name' | grep -qx "$f" \
      || gh release upload "$our_tag" -R "$repo" "$work/$f"
  done
else
  ffmpeg_rev=$(printf '%s' "$amd64_file" | sed -n 's/^ffmpeg-\(N-[0-9]*-g[0-9a-f]*\)-.*/\1/p')
  gh release create "$our_tag" -R "$repo" --latest=false \
    --title "ffmpeg linux ${btbn_tag#autobuild-} (BtbN mirror)" \
    --notes "$(cat <<EOF
Unmodified mirror of the linux64 / linuxarm64 GPL shared builds from
[BtbN/FFmpeg-Builds $btbn_tag](https://github.com/BtbN/FFmpeg-Builds/releases/tag/$btbn_tag),
pinned by \`Dockerfile\` and \`Dockerfile.gpu\` so image builds do not depend on
upstream retention. FFmpeg revision \`$ffmpeg_rev\`; the build recipe and the
sources of every bundled library are in
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds).

| asset | sha256 |
| --- | --- |
| \`$amd64_file\` | \`$amd64_sha\` |
| \`$arm64_file\` | \`$arm64_sha\` |

Created by \`scripts/mirror_ffmpeg_linux.sh $btbn_tag\`.
EOF
)" \
    "$work/$amd64_file" "$work/$arm64_file"
fi

for df in "$root/Dockerfile" "$root/Dockerfile.gpu"; do
  sed -i.bak -E \
    -e "s|^ARG FFMPEG_RELEASE=.*|ARG FFMPEG_RELEASE=$our_tag|" \
    -e "s|(amd64\) ff_file=)[^;]*;|\1$amd64_file;|" \
    -e "s|(arm64\) ff_file=)[^;]*;|\1$arm64_file;|" \
    "$df"
  # The sha lines follow their ff_file line; rewrite them by arch.
  awk -v a="$amd64_sha" -v b="$arm64_sha" '
    /amd64\) ff_file=/ { arch = "amd64" }
    /arm64\) ff_file=/ { arch = "arm64" }
    /ff_sha=/ && arch != "" { sub(/ff_sha=[0-9a-f]+/, "ff_sha=" (arch == "amd64" ? a : b)); arch = "" }
    { print }
  ' "$df" > "$df.tmp" && mv "$df.tmp" "$df"
  rm -f "$df.bak"
done

echo
echo "Dockerfiles now pin $our_tag:"
grep -n "FFMPEG_RELEASE=\|ff_file=\|ff_sha=" "$root/Dockerfile" "$root/Dockerfile.gpu"
