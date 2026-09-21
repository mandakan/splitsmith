#!/usr/bin/env bash
# Build a static ffmpeg + ffprobe for macOS arm64 from pinned sources.
#
#   desktop/build-ffmpeg.sh               GPL build (libx264 + everything the pipeline uses)
#   desktop/build-ffmpeg.sh --lgpl        LGPL build (no --enable-gpl, no libx264)
#   desktop/build-ffmpeg.sh --record-pins fill empty sha256 cells in ffmpeg-pins.lock
#   desktop/build-ffmpeg.sh --clean       rebuild the dependency prefix too
#
# "Static" means: no dylib outside /usr/lib and /System/Library. zlib,
# bz2 and iconv come from the OS (lzma has no header in the SDK and
# nothing in the pipeline reads xz or tiff); everything else is linked
# in. The external libraries are exactly the ones the pipeline uses:
# x264 (GPL variant only), freetype and harfbuzz for drawtext, plus
# Apple's VideoToolbox and AudioToolbox. No fontconfig: every drawtext in
# the pipeline names ``fontfile=`` (overlay_clock, mp4_grid), so the
# ``font=`` name lookup fontconfig provides is never used, and its build
# wants to own /etc/fonts.
#
# Build-host tools (never shipped):
#   brew install nasm pkg-config meson ninja autoconf automake libtool
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PINS="$HERE/ffmpeg-pins.lock"
BUILD="$HERE/build"
SRC="$BUILD/ffmpeg-src"
PREFIX="$BUILD/ffmpeg-prefix"
OUT="$BUILD/ffmpeg-out"
VARIANT=gpl
RECORD=0
CLEAN=0
for arg in "$@"; do
  case "$arg" in
    --lgpl) VARIANT=lgpl ;;
    --record-pins) RECORD=1 ;;
    --clean) CLEAN=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

for tool in nasm pkg-config meson ninja autoconf automake glibtoolize; do
  command -v "$tool" >/dev/null || {
    echo "missing $tool: brew install nasm pkg-config meson ninja autoconf automake libtool" >&2
    exit 1
  }
done
[ "$(uname -m)" = arm64 ] || { echo "arm64 host required" >&2; exit 1; }

[ "$CLEAN" = 1 ] && rm -rf "$PREFIX"
rm -rf "$OUT"
mkdir -p "$SRC" "$PREFIX" "$OUT"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export MACOSX_DEPLOYMENT_TARGET=12.0
JOBS="$(sysctl -n hw.ncpu)"

# --- pins -------------------------------------------------------------
pin() { awk -F'\t' -v n="$1" '$1==n {print $2, $3, $4}' "$PINS"; }
fetch() {  # name -> extracted dir path on stdout
  local name="$1" version url sha
  read -r version url sha < <(pin "$name")
  [ -n "$url" ] || { echo "no pin for $name" >&2; exit 1; }
  local tarball="$SRC/$(basename "$url")"
  [ -f "$tarball" ] || curl -fsSL -o "$tarball" "$url"
  local got; got="$(shasum -a 256 "$tarball" | cut -d' ' -f1)"
  if [ "$sha" = "-" ]; then
    [ "$RECORD" = 1 ] || { echo "$name has no sha256 in $PINS; run with --record-pins" >&2; exit 1; }
    awk -F'\t' -v n="$name" -v h="$got" 'BEGIN{OFS="\t"} $1==n {$4=h} {print}' "$PINS" > "$PINS.tmp" \
      && mv "$PINS.tmp" "$PINS"
  elif [ "$sha" != "$got" ]; then
    echo "$name: sha256 mismatch (pinned $sha, got $got)" >&2; exit 1
  fi
  local dir="$SRC/$name-$version"
  if [ ! -d "$dir" ]; then
    mkdir -p "$dir"; tar -xf "$tarball" -C "$dir" --strip-components=1
  fi
  echo "$dir"
}

log() { echo "== $*" >&2; }

# --- deps -------------------------------------------------------------
built() { [ -f "$PREFIX/lib/pkgconfig/$1.pc" ] && { log "$1 already in prefix"; return 0; } || return 1; }
build_freetype() {
  built freetype2 && return
  local d; d="$(fetch freetype)"; log "freetype in $d"; pushd "$d" >/dev/null
  ./configure --prefix="$PREFIX" --enable-static --disable-shared \
    --with-harfbuzz=no --with-png=no --with-brotli=no --with-bzip2=yes --with-zlib=yes
  make -j"$JOBS" && make install; popd >/dev/null
}
build_harfbuzz() {
  built harfbuzz && return
  local d; d="$(fetch harfbuzz)"; log "harfbuzz in $d"; pushd "$d" >/dev/null
  rm -rf build
  meson setup build --prefix="$PREFIX" --default-library=static --buildtype=release \
    -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dchafa=disabled \
    -Dicu=disabled -Dtests=disabled -Ddocs=disabled -Dutilities=disabled
  ninja -C build && ninja -C build install; popd >/dev/null
}
build_x264() {
  built x264 && return
  local d; d="$(fetch x264)"; log "x264 in $d"; pushd "$d" >/dev/null
  ./configure --prefix="$PREFIX" --enable-static --disable-shared --disable-cli --enable-pic
  make -j"$JOBS" && make install; popd >/dev/null
}

build_freetype; build_harfbuzz
if [ "$VARIANT" = gpl ]; then build_x264; fi

# --- ffmpeg -----------------------------------------------------------
FFDIR="$(fetch ffmpeg)"; log "ffmpeg in $FFDIR"
CONFIGURE=(
  ./configure --prefix="$PREFIX" --pkg-config-flags=--static
  --disable-shared --enable-static --disable-doc --disable-debug
  --disable-ffplay --enable-ffprobe
  # No autodetection: a Homebrew host otherwise links X11/xcb/SDL2 dylibs
  # into the binary. Everything external is named here.
  --disable-autodetect
  --enable-zlib --enable-bzlib --enable-iconv
  --enable-videotoolbox --enable-audiotoolbox --enable-avfoundation --enable-coreimage
  --enable-libfreetype --enable-libharfbuzz
  --extra-ldflags="-L$PREFIX/lib" --extra-cflags="-I$PREFIX/include"
  --extra-libs="-lc++ -liconv -lbz2 -lz"
)
if [ "$VARIANT" = gpl ]; then CONFIGURE+=(--enable-gpl --enable-libx264); fi
pushd "$FFDIR" >/dev/null
make distclean >/dev/null 2>&1 || true
"${CONFIGURE[@]}"
make -j"$JOBS"
cp ffmpeg ffprobe "$OUT/"
popd >/dev/null

# --- verify and pack --------------------------------------------------
for bin in ffmpeg ffprobe; do
  if otool -L "$OUT/$bin" | tail -n +2 | grep -v -E '^\s+(/usr/lib/|/System/Library/)'; then
    echo "$bin links a non-system dylib" >&2; exit 1
  fi
done
# Capture first: ``ffmpeg | grep -q`` would SIGPIPE ffmpeg on the first
# match and, under pipefail, fail the script with no message.
encoders="$("$OUT/ffmpeg" -hide_banner -encoders)"
filters="$("$OUT/ffmpeg" -hide_banner -filters)"
grep -q h264_videotoolbox <<<"$encoders" || { echo "no h264_videotoolbox encoder" >&2; exit 1; }
grep -q drawtext <<<"$filters" || { echo "no drawtext filter" >&2; exit 1; }
if [ "$VARIANT" = gpl ]; then
  grep -q libx264 <<<"$encoders" || { echo "gpl build lacks libx264" >&2; exit 1; }
else
  if grep -q libx264 <<<"$encoders"; then echo "lgpl build contains libx264" >&2; exit 1; fi
fi

FFVER="$(pin ffmpeg | cut -d' ' -f1)"
printf '%s\n' "${CONFIGURE[@]}" > "$OUT/BUILD-RECIPE.txt"
grep -v '^#' "$PINS" > "$OUT/SOURCES.txt"
tar -czf "$BUILD/ffmpeg-macos-arm64-$FFVER-$VARIANT.tar.gz" -C "$OUT" ffmpeg ffprobe BUILD-RECIPE.txt SOURCES.txt
echo "built $BUILD/ffmpeg-macos-arm64-$FFVER-$VARIANT.tar.gz"
