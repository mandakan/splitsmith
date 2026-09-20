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

wheel="$(ls -t "$ROOT"/dist/splitsmith-*.whl 2>/dev/null | head -1 || true)"
[ -n "$wheel" ] || { echo "no wheel in $ROOT/dist; run 'uv build --wheel' first" >&2; exit 1; }

rm -rf "$RUNTIME"; mkdir -p "$RUNTIME"
# uv lays the interpreter out as <install-dir>/cpython-<full>-macos-aarch64-none/;
# rename to a stable 'python' so electron-builder and main.ts have a fixed path.
UV_PYTHON_INSTALL_DIR="$RUNTIME" uv python install "$PYVER"
src="$(ls -d "$RUNTIME"/cpython-"$PYVER".*-macos-aarch64-none)"
mv "$src" "$RUNTIME/python"
PY="$RUNTIME/python/bin/python$PYVER"

uv pip install --python "$PY" --break-system-packages --no-cache "$wheel"

# Sentinels: the hosted extra must not be present, the SPA must be.
if "$PY" -c "import psycopg" 2>/dev/null; then
  echo "psycopg is installed; the bundle must not carry the hosted extra" >&2; exit 1
fi
"$PY" -c "import splitsmith.ui.server as s, sys; sys.exit(0 if s.STATIC_DIR.joinpath('index.html').exists() else 1)" \
  || { echo "wheel has no SPA dist" >&2; exit 1; }

# Console scripts get an absolute shebang pointing at this build dir. Make
# them relocatable with the sh/python polyglot pip uses: sh reads the
# second line as "exec ..." (two empty quotes then exec), python reads it
# as the start of a triple-quoted string that the third line closes.
# readlink -f resolves the /usr/local/bin symlink the app installs back
# into the bundle (macOS 12.3+; LSMinimumSystemVersion is set to match).
SHIM_HEAD=$(cat <<'EOF'
#!/bin/sh
'''exec' "$(dirname "$(readlink -f "$0")")/python3.12" "$0" "$@"
' '''
EOF
)
for script in "$RUNTIME"/python/bin/splitsmith*; do
  [ -f "$script" ] || continue
  if head -1 "$script" | grep -q "^#!$RUNTIME"; then
    { printf '%s\n' "$SHIM_HEAD"; tail -n +2 "$script"; } > "$script.tmp"
    mv "$script.tmp" "$script"; chmod +x "$script"
  fi
done

# Precompile so the sealed bundle is never written to at runtime.
"$PY" -m compileall -q "$RUNTIME/python/lib/python$PYVER/site-packages" >/dev/null
# Drop what the sealed bundle never uses: test trees, the installer stack
# (nothing installs into a signed app), Tk and the C headers. The two big
# items that stay are known: Playwright's Node driver (#986 replaces the
# rasterizer with Electron's own Chromium) and llvmlite under librosa (#442).
find "$RUNTIME/python/lib/python$PYVER/site-packages" -type d -name tests -prune -exec rm -rf {} +
rm -rf "$RUNTIME/python/lib/python$PYVER/test" \
  "$RUNTIME/python/lib/python$PYVER/site-packages"/pip "$RUNTIME/python/lib/python$PYVER/site-packages"/pip-*.dist-info \
  "$RUNTIME/python/lib/python$PYVER/ensurepip" "$RUNTIME/python/lib/python$PYVER/idlelib" \
  "$RUNTIME/python/lib/python$PYVER/tkinter" "$RUNTIME/python/lib/python$PYVER/lib-dynload"/_tkinter*.so \
  "$RUNTIME"/python/lib/tcl* "$RUNTIME"/python/lib/tk* "$RUNTIME"/python/lib/libtcl* "$RUNTIME"/python/lib/libtk* \
  "$RUNTIME/python/include" "$RUNTIME/python/share"
rm -f "$RUNTIME"/python/bin/pip* "$RUNTIME"/python/bin/idle* "$RUNTIME"/python/bin/2to3*
echo "runtime at $RUNTIME/python ($(du -sh "$RUNTIME/python" | cut -f1))"
