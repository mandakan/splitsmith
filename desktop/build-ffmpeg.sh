#!/usr/bin/env bash
# Build a static ffmpeg + ffprobe for macOS arm64, x86_64 Linux or x86_64
# Windows from pinned sources. On Linux run it through build-ffmpeg-linux.sh,
# which builds inside ubuntu:22.04 so the binary links against the app's glibc
# floor. Windows is cross-compiled with mingw-w64 on Linux (any arch) through
# build-ffmpeg-windows.sh.
#
#   desktop/build-ffmpeg.sh               GPL build (libx264 + everything the pipeline uses)
#   desktop/build-ffmpeg.sh --lgpl        LGPL build (no --enable-gpl, no libx264)
#   desktop/build-ffmpeg.sh --windows     cross-compile ffmpeg.exe + ffprobe.exe
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
# On Linux "static" means: nothing but glibc (ldd allowlist below); zlib,
# bz2 and libstdc++ are linked from their .a archives.
#
# On Windows it means: no DLL outside Windows itself (objdump allowlist
# below). mingw has no system zlib, so zlib is built from its pin (and only
# here); bz2 and iconv are left out, since nothing in the pipeline reads bz2
# or converts subtitle charsets. Threads are ffmpeg's own w32threads, so there
# is no winpthread to ship. The .exe files cannot run on the build host, so the
# encoder and filter checks read ffmpeg's generated config instead; CI runs the
# binaries on windows-latest (desktop-windows.yml).
#
# Build-host tools (never shipped):
#   brew install nasm pkg-config meson ninja autoconf automake libtool
#   apt-get install nasm pkg-config meson ninja-build autoconf automake libtool
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
WINDOWS=0
for arg in "$@"; do
  case "$arg" in
    --lgpl) VARIANT=lgpl ;;
    --windows) WINDOWS=1 ;;
    --record-pins) RECORD=1 ;;
    --clean) CLEAN=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

OS="$(uname -s)"
CROSS=x86_64-w64-mingw32
if [ "$WINDOWS" = 1 ]; then
  [ "$OS" = Linux ] || { echo "--windows cross-compiles on Linux only (build-ffmpeg-windows.sh)" >&2; exit 1; }
  for tool in nasm pkg-config meson ninja autoconf automake libtoolize "$CROSS-gcc" "$CROSS-g++"; do
    command -v "$tool" >/dev/null || {
      echo "missing $tool: apt-get install nasm pkg-config meson ninja-build autoconf automake libtool mingw-w64" >&2
      exit 1
    }
  done
  # Separate trees: the deps build in their source dirs, and a native
  # build's objects there must not end up in the .exe (or the reverse).
  SRC="$BUILD/ffmpeg-src-windows"
  PREFIX="$BUILD/ffmpeg-prefix-windows"
  OUT="$BUILD/ffmpeg-out-windows"
elif [ "$OS" = Darwin ]; then
  for tool in nasm pkg-config meson ninja autoconf automake glibtoolize; do
    command -v "$tool" >/dev/null || {
      echo "missing $tool: brew install nasm pkg-config meson ninja autoconf automake libtool" >&2
      exit 1
    }
  done
  [ "$(uname -m)" = arm64 ] || { echo "arm64 host required" >&2; exit 1; }
else
  for tool in nasm pkg-config meson ninja autoconf automake libtoolize; do
    command -v "$tool" >/dev/null || {
      echo "missing $tool: apt-get install nasm pkg-config meson ninja-build autoconf automake libtool" >&2
      exit 1
    }
  done
  [ "$(uname -m)" = x86_64 ] || { echo "x86_64 host required" >&2; exit 1; }
fi

[ "$CLEAN" = 1 ] && rm -rf "$PREFIX"
rm -rf "$OUT"
mkdir -p "$SRC" "$PREFIX" "$OUT"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
if [ "$WINDOWS" = 1 ]; then
  # Only the prefix: the host's .pc files describe Linux libraries.
  export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"
  JOBS="$(nproc)"
  MESON_LIBDIR=--libdir=lib
  MESON_CROSS="$BUILD/mingw-w64.meson"
  cat > "$MESON_CROSS" <<EOF
[binaries]
c = '$CROSS-gcc'
cpp = '$CROSS-g++'
ar = '$CROSS-ar'
strip = '$CROSS-strip'
windres = '$CROSS-windres'
pkg-config = 'pkg-config'

[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
EOF
elif [ "$OS" = Darwin ]; then
  export MACOSX_DEPLOYMENT_TARGET=12.0
  JOBS="$(sysctl -n hw.ncpu)"
else
  JOBS="$(nproc)"
  # Debian's meson defaults libdir to lib/x86_64-linux-gnu, where neither
  # PKG_CONFIG_PATH nor -L$PREFIX/lib looks.
  MESON_LIBDIR=--libdir=lib
fi

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
build_zlib() {  # Windows only: the other targets link the OS's zlib
  built zlib && return
  local d; d="$(fetch zlib)"; log "zlib in $d"; pushd "$d" >/dev/null
  make -f win32/Makefile.gcc clean >/dev/null 2>&1 || true
  make -f win32/Makefile.gcc -j"$JOBS" PREFIX="$CROSS-" libz.a
  install -D -m644 libz.a "$PREFIX/lib/libz.a"
  install -D -m644 zlib.h "$PREFIX/include/zlib.h"
  install -D -m644 zconf.h "$PREFIX/include/zconf.h"
  mkdir -p "$PREFIX/lib/pkgconfig"
  printf 'prefix=%s\nName: zlib\nDescription: zlib\nVersion: %s\nLibs: -L${prefix}/lib -lz\nCflags: -I${prefix}/include\n' \
    "$PREFIX" "$(pin zlib | cut -d' ' -f1)" > "$PREFIX/lib/pkgconfig/zlib.pc"
  popd >/dev/null
}
build_freetype() {
  built freetype2 && return
  local d; d="$(fetch freetype)"; log "freetype in $d"; pushd "$d" >/dev/null
  local host=() bzip2=yes
  if [ "$WINDOWS" = 1 ]; then host=(--host="$CROSS"); bzip2=no; fi
  ./configure ${host[@]+"${host[@]}"} --prefix="$PREFIX" --enable-static --disable-shared \
    --with-harfbuzz=no --with-png=no --with-brotli=no --with-bzip2="$bzip2" --with-zlib=yes
  make -j"$JOBS" && make install; popd >/dev/null
}
build_harfbuzz() {
  built harfbuzz && return
  local d; d="$(fetch harfbuzz)"; log "harfbuzz in $d"; pushd "$d" >/dev/null
  rm -rf build
  meson setup build ${MESON_CROSS:+--cross-file="$MESON_CROSS"} \
    --prefix="$PREFIX" ${MESON_LIBDIR:+"$MESON_LIBDIR"} --default-library=static --buildtype=release \
    -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dchafa=disabled \
    -Dicu=disabled -Dtests=disabled -Ddocs=disabled -Dutilities=disabled
  ninja -C build && ninja -C build install; popd >/dev/null
}
build_x264() {
  built x264 && return
  local d; d="$(fetch x264)"; log "x264 in $d"; pushd "$d" >/dev/null
  local host=()
  [ "$WINDOWS" = 1 ] && host=(--host="$CROSS" --cross-prefix="$CROSS-")
  make distclean >/dev/null 2>&1 || true
  ./configure ${host[@]+"${host[@]}"} --prefix="$PREFIX" --enable-static --disable-shared --disable-cli --enable-pic
  make -j"$JOBS" && make install; popd >/dev/null
}

if [ "$WINDOWS" = 1 ]; then build_zlib; fi
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
)
if [ "$WINDOWS" = 1 ]; then
  # --disable-autodetect also drops the thread backend; name it.
  CONFIGURE+=(--enable-cross-compile --arch=x86_64 --target-os=mingw32 --cross-prefix="$CROSS-"
    --pkg-config=pkg-config --enable-zlib --enable-w32threads
    # vfwcap and gdigrab: screen and camera capture the app never does,
    # and avicap32.dll imports.
    --disable-indevs --disable-outdevs)
else
  CONFIGURE+=(--enable-zlib --enable-bzlib --enable-iconv)
fi
if [ "$OS" = Darwin ]; then
  CONFIGURE+=(--enable-videotoolbox --enable-audiotoolbox --enable-avfoundation --enable-coreimage)
fi
CONFIGURE+=(--enable-libfreetype --enable-libharfbuzz)
if [ "$WINDOWS" = 1 ]; then
  # -static: libgcc, libstdc++ (harfbuzz) and anything else the toolchain
  # would otherwise load as a DLL beside the .exe.
  CONFIGURE+=(--extra-ldflags="-L$PREFIX/lib -static -static-libgcc -static-libstdc++"
    --extra-cflags="-I$PREFIX/include" --extra-libs="-lstdc++ -lz")
elif [ "$OS" = Darwin ]; then
  CONFIGURE+=(--extra-ldflags="-L$PREFIX/lib" --extra-cflags="-I$PREFIX/include"
    --extra-libs="-lc++ -liconv -lbz2 -lz")
else
  # zlib and bz2 static from the distro: their .a files are copied into
  # $PREFIX/lib, which ld searches before the system dirs, so ld finds the
  # archive there before it ever sees libz.so. libstdc++ (harfbuzz) by
  # file name for the same reason. glibc provides iconv.
  cp /usr/lib/x86_64-linux-gnu/libz.a /usr/lib/x86_64-linux-gnu/libbz2.a "$PREFIX/lib/"
  CONFIGURE+=(--extra-ldflags="-L$PREFIX/lib -static-libgcc" --extra-cflags="-I$PREFIX/include"
    --extra-libs="-l:libstdc++.a -lbz2 -lz -lm -lpthread")
fi
if [ "$VARIANT" = gpl ]; then CONFIGURE+=(--enable-gpl --enable-libx264); fi
pushd "$FFDIR" >/dev/null
make distclean >/dev/null 2>&1 || true
"${CONFIGURE[@]}"
make -j"$JOBS"
EXE=""; [ "$WINDOWS" = 1 ] && EXE=.exe
cp "ffmpeg$EXE" "ffprobe$EXE" "$OUT/"
popd >/dev/null

# --- verify and pack --------------------------------------------------
for bin in "ffmpeg$EXE" "ffprobe$EXE"; do
  if [ "$WINDOWS" = 1 ]; then
    allowed='^(kernel32|user32|advapi32|bcrypt|ole32|oleaut32|shell32|shlwapi|ws2_32|secur32|psapi|gdi32|msvcrt|api-ms-win-[a-z0-9-]+)\.dll$'
    if "$CROSS-objdump" -p "$OUT/$bin" | sed -n 's/^\s*DLL Name: //p' | tr 'A-Z' 'a-z' | grep -v -E "$allowed"; then
      echo "$bin imports a non-system DLL" >&2; exit 1
    fi
  elif [ "$OS" = Darwin ]; then
    if otool -L "$OUT/$bin" | tail -n +2 | grep -v -E '^\s+(/usr/lib/|/System/Library/)'; then
      echo "$bin links a non-system dylib" >&2; exit 1
    fi
  else
    allowed='linux-vdso|libc\.so|libm\.so|libmvec\.so|libpthread\.so|libdl\.so|librt\.so|ld-linux-x86-64'
    if ldd "$OUT/$bin" | grep -v -E "$allowed"; then echo "$bin links a non-glibc library" >&2; exit 1; fi
  fi
done
if [ "$WINDOWS" = 1 ]; then
  # What -encoders / -filters would list, from the generated config.
  components="$(cat "$FFDIR/libavcodec/codec_list.c" "$FFDIR/libavfilter/filter_list.c")"
  encoders="$(sed -n 's/.*&ff_\(.*\)_encoder,.*/\1/p' <<<"$components")"
  filters="$(sed -n 's/.*&ff_[av]f_\(.*\),.*/\1/p' <<<"$components")"
else
  # Capture first: ``ffmpeg | grep -q`` would SIGPIPE ffmpeg on the first
  # match and, under pipefail, fail the script with no message.
  encoders="$("$OUT/ffmpeg" -hide_banner -encoders)"
  filters="$("$OUT/ffmpeg" -hide_banner -filters)"
fi
if [ "$OS" = Darwin ]; then
  grep -q h264_videotoolbox <<<"$encoders" || { echo "no h264_videotoolbox encoder" >&2; exit 1; }
fi
grep -q drawtext <<<"$filters" || { echo "no drawtext filter" >&2; exit 1; }
if [ "$VARIANT" = gpl ]; then
  grep -q libx264 <<<"$encoders" || { echo "gpl build lacks libx264" >&2; exit 1; }
else
  if grep -q libx264 <<<"$encoders"; then echo "lgpl build contains libx264" >&2; exit 1; fi
fi

FFVER="$(pin ffmpeg | cut -d' ' -f1)"
printf '%s\n' "${CONFIGURE[@]}" > "$OUT/BUILD-RECIPE.txt"
grep -v '^#' "$PINS" > "$OUT/SOURCES.txt"
if [ "$WINDOWS" = 1 ]; then
  TARBALL="$BUILD/ffmpeg-windows-x86_64-$FFVER-$VARIANT.tar.gz"
elif [ "$OS" = Darwin ]; then
  TARBALL="$BUILD/ffmpeg-macos-arm64-$FFVER-$VARIANT.tar.gz"
else
  TARBALL="$BUILD/ffmpeg-linux-x86_64-$FFVER-$VARIANT.tar.gz"
fi
tar -czf "$TARBALL" -C "$OUT" "ffmpeg$EXE" "ffprobe$EXE" BUILD-RECIPE.txt SOURCES.txt
echo "built $TARBALL"
