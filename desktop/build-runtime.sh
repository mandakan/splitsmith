#!/usr/bin/env bash
# Assemble the bundled Python runtime: python-build-standalone 3.12 + the
# splitsmith wheel installed into its own site-packages. No extras: the
# hosted stack (psycopg) must never enter the bundle. What ships is what
# uv.lock resolves for the default dependency set.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
RUNTIME="$HERE/build/runtime"
PYVER="3.12"

# shellcheck source=lib/target.sh
source "$HERE/lib/target.sh"
TARGET="$(host_target)"
wheel=""
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --wheel) wheel="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$wheel" ] || wheel="$(ls -t "$ROOT"/dist/splitsmith-*.whl 2>/dev/null | head -1 || true)"
[ -n "$wheel" ] || { echo "no wheel in $ROOT/dist; run 'uv build --wheel' first" >&2; exit 1; }
[ -f "$wheel" ] || { echo "no such wheel: $wheel" >&2; exit 1; }

# uv lays the interpreter out as <install-dir>/cpython-<full>-<platform>/;
# rename to a stable 'python' so electron-builder and main.ts have a fixed path.
# Linux names the baseline x86_64 build explicitly: on a v3-capable host uv
# could otherwise pick an x86_64_v3 build that dies on older CPUs.
case "$TARGET" in
  macos-aarch64) request="$PYVER"; layout="macos-aarch64-none" ;;
  linux-x86_64) request="cpython-$PYVER-linux-x86_64-gnu"; layout="linux-x86_64-gnu" ;;
  windows-x86_64) request="cpython-$PYVER-windows-x86_64-none"; layout="windows-x86_64-none" ;;
  *) echo "unknown target $TARGET" >&2; exit 2 ;;
esac
rm -rf "$RUNTIME"; mkdir -p "$RUNTIME"
UV_PYTHON_INSTALL_DIR="$RUNTIME" uv python install "$request"
src="$(ls -d "$RUNTIME"/cpython-"$PYVER".*-"$layout")"
mv "$src" "$RUNTIME/python"
# python-build-standalone's Windows layout has no bin/: python.exe at the
# root, the stdlib in Lib\, console scripts in Scripts\.
if [ "$TARGET" = windows-x86_64 ]; then
  PY="$RUNTIME/python/python.exe"; STDLIB="$RUNTIME/python/Lib"; SCRIPTS="$RUNTIME/python/Scripts"
else
  PY="$RUNTIME/python/bin/python$PYVER"; STDLIB="$RUNTIME/python/lib/python$PYVER"; SCRIPTS="$RUNTIME/python/bin"
fi
SITE="$STDLIB/site-packages"

uv pip install --python "$PY" --break-system-packages --no-cache "$wheel"

# Sentinels: the hosted extra must not be present, the SPA must be.
if "$PY" -c "import psycopg" 2>/dev/null; then
  echo "psycopg is installed; the bundle must not carry the hosted extra" >&2; exit 1
fi
"$PY" -c "import splitsmith.ui.server as s, sys; sys.exit(0 if s.STATIC_DIR.joinpath('index.html').exists() else 1)" \
  || { echo "wheel has no SPA dist" >&2; exit 1; }
# A release build must ship the YouTube OAuth client publish-pypi bakes into
# the wheel; a checkout-built wheel has empty constants (the 0.53.0 DMG did).
# release.sh and desktop-linux.yml's tagged run set this; dev builds do not.
if [ "${SPLITSMITH_REQUIRE_YOUTUBE_CLIENT:-0}" = 1 ]; then
  "$PY" "$HERE/scripts/check_youtube_client.py"
fi

# Console scripts name this build dir's interpreter: as a shebang, or, once
# the path passes uv's shebang length limit (release.sh's $TMPDIR worktree
# does; CI's checkout does not), as this same polyglot with the absolute
# path on line 2. Make both relocatable with the sh/python polyglot pip
# uses: sh reads the second line as "exec ..." (two empty quotes then
# exec), python reads it as the start of a triple-quoted string that the
# third line closes. readlink -f resolves the /usr/local/bin symlink the
# app installs back into the bundle (macOS 12.3+; LSMinimumSystemVersion
# is set to match).
if [ "$TARGET" = windows-x86_64 ]; then
  # uv's Scripts\*.exe launchers carry the interpreter's absolute path and
  # cannot be rewritten in place; ours become .cmd shims that find
  # python.exe relative to themselves, and every other launcher goes (the
  # bundle runs none of them).
  "$PY" "$HERE/scripts/windows_console_shims.py" splitsmith
else
SHIM_HEAD=$(cat <<'EOF'
#!/bin/sh
'''exec' "$(dirname "$(readlink -f "$0")")/python3.12" "$0" "$@"
' '''
EOF
)
for script in "$RUNTIME"/python/bin/*; do
  [ -f "$script" ] && [ ! -L "$script" ] || continue
  body=""
  if head -1 "$script" | grep -q "^#!$RUNTIME"; then
    body=2
  elif [ "$(head -1 "$script")" = "#!/bin/sh" ] && sed -n 2p "$script" | grep -qF "'''exec' '$RUNTIME/"; then
    body=4
  fi
  if [ -n "$body" ]; then
    { printf '%s\n' "$SHIM_HEAD"; tail -n +"$body" "$script"; } > "$script.tmp"
    mv "$script.tmp" "$script"; chmod +x "$script"
  fi
done
fi
# The bundle outlives this dir: a script still naming it works here (and in
# smoke.sh, run before the worktree goes) and nowhere else (0.87.2's CLI).
# On Windows the dir is also spelled C:\..., which is what a launcher holds.
build_dirs=(-e "$RUNTIME")
if command -v cygpath >/dev/null; then build_dirs+=(-e "$(cygpath -w "$RUNTIME")" -e "$(cygpath -m "$RUNTIME")"); fi
if stale="$(grep -ilF "${build_dirs[@]}" "$SCRIPTS"/* 2>/dev/null)"; then
  echo "console scripts still name the build dir:" >&2; echo "$stale" >&2; exit 1
fi

# Precompile so the sealed bundle is never written to at runtime.
"$PY" -m compileall -q "$SITE" >/dev/null
# Drop what the sealed bundle never uses: test trees, the installer stack
# (nothing installs into a signed app), Tk and the C headers. The two big
# items that stay are known: Playwright's Node driver (#986 replaces the
# rasterizer with Electron's own Chromium) and llvmlite under librosa (#442).
find "$SITE" -type d -name tests -prune -exec rm -rf {} +
rm -rf "$STDLIB/test" "$SITE"/pip "$SITE"/pip-*.dist-info \
  "$STDLIB/ensurepip" "$STDLIB/idlelib" "$STDLIB/tkinter" "$STDLIB/turtledemo" \
  "$STDLIB/lib-dynload"/_tkinter*.so \
  "$RUNTIME"/python/lib/tcl* "$RUNTIME"/python/lib/tk* "$RUNTIME"/python/lib/libtcl* "$RUNTIME"/python/lib/libtk* \
  "$RUNTIME/python/include" "$RUNTIME/python/share" \
  "$RUNTIME/python/tcl" "$RUNTIME/python/libs" "$RUNTIME/python/DLLs"/_tkinter.pyd \
  "$RUNTIME/python/DLLs"/tcl*.dll "$RUNTIME/python/DLLs"/tk*.dll
rm -f "$SCRIPTS"/pip* "$SCRIPTS"/idle* "$SCRIPTS"/2to3*
echo "runtime at $RUNTIME/python ($(du -sh "$RUNTIME/python" | cut -f1))"
