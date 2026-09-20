# Electron Desktop Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed, notarized `Splitsmith-<version>-arm64.dmg` whose Electron shell launches the existing engine from a bundled Python runtime and shows the existing SPA.

**Architecture:** `desktop/` holds an Electron main process (TypeScript, compiled by `tsc`) that spawns `python3.12 -m splitsmith.ui.embedded` from `Contents/Resources/python`, parses the `SPLITSMITH_READY` banner and loads the sidecar's URL. Build scripts assemble the runtime (python-build-standalone + the wheel), fetch our own static ffmpeg, generate the notices file, and hand everything to electron-builder. The engine gains one route family (`/api/system/chromium`) so the app can install Playwright's browser without a terminal.

**Tech Stack:** Electron, electron-builder (+ its notarize support), TypeScript, vitest (shell unit tests), uv + python-build-standalone 3.12, FFmpeg built from source, FastAPI route + pytest, React + vitest for the SPA button.

**Spec:** `docs/superpowers/specs/2026-09-20-electron-desktop-packaging-design.md`

## Global Constraints

- macOS Apple Silicon (arm64) only; no Intel, no Windows.
- Python runtime is python-build-standalone 3.12 via `uv python install`, not PyInstaller.
- No extras installed into the bundle: the wheel's default dependency set only (`psycopg` must never be present).
- Data location is `~/.splitsmith`, shared with the CLI; the app never uses `~/Library/Application Support` for engine state.
- The bundle is never written after signing: `PYTHONDONTWRITEBYTECODE=1`, `compileall` at build time, `NUMBA_CACHE_DIR=~/Library/Caches/Splitsmith/numba`.
- Entitlements are exactly `com.apple.security.cs.allow-jit` and `com.apple.security.cs.allow-unsigned-executable-memory`.
- ffmpeg is built by `desktop/build-ffmpeg.sh` with `--enable-gpl --enable-libx264 --enable-videotoolbox --enable-audiotoolbox --enable-libfreetype --enable-libharfbuzz --enable-libfontconfig` and nothing else external; `--lgpl` drops `--enable-gpl` and libx264.
- Signing and notarization run locally; CI builds unsigned (`CSC_IDENTITY_AUTO_DISCOVERY=false`).
- The new engine routes are local mode only; hosted answers 404.
- Repo conventions: Python 3.11+ type hints, Pydantic at module boundaries, Black 110, Ruff, `pathlib.Path`; SPA on the `components/ui` primitives; ASCII punctuation, no em dashes, in every file written.
- `uv` for all Python dependency work; never `pip`. `pnpm` for Node.
- Commit after every task with the attribution lines from the session's system reminder.

---

## File map

Created:

- `desktop/package.json` (private, `"version": "0.0.0"`, scripts, devDependencies)
- `desktop/pnpm-lock.yaml`
- `desktop/tsconfig.json`
- `desktop/vitest.config.ts`
- `desktop/electron-builder.yml`
- `desktop/entitlements.plist`
- `desktop/src/main.ts` (Electron wiring only)
- `desktop/src/sidecar.ts` (pure: READY parsing, env/argv construction, external-URL test)
- `desktop/src/sidecar.test.ts`
- `desktop/src/cliLink.ts` (pure: symlink plan) and `desktop/src/cliLink.test.ts`
- `desktop/src/menu.ts` (application menu)
- `desktop/src/preload.ts`
- `desktop/src/loading.html`
- `desktop/build-ffmpeg.sh`, `desktop/ffmpeg-pins.lock`
- `desktop/fetch-ffmpeg.sh`
- `desktop/build-runtime.sh`
- `desktop/build-notices.sh`, `desktop/NOTICES.head.md`
- `desktop/build.sh` (the six-step pipeline; `pnpm build` calls it)
- `desktop/verify-signed.sh`
- `desktop/smoke.sh`
- `src/splitsmith/ui/system_api.py`, `tests/test_system_api.py`
- `.github/workflows/desktop.yml`

Modified:

- `src/splitsmith/ui/server.py` (include the router; one line next to `app.include_router(youtube_router)`)
- `src/splitsmith/ui_static/src/lib/api.ts` (two client calls)
- `src/splitsmith/ui_static/src/components/export/PreviewPane.tsx` and `.test.tsx` (the install button)
- `src/splitsmith/ui_static/src/lib/exportPreview.ts` (no change to `previewLine`; the button is a sibling)
- `README.md` (a "Desktop app" subsection under Install)
- `CLAUDE.md` (a "Desktop app (desktop/)" section)
- `.gitignore` (nothing: root `dist/` and `build/` are unanchored and already cover `desktop/build` and `desktop/dist`; verify in Task 2)

---

### Task 1: ffmpeg build script and pins

**Files:**
- Create: `desktop/build-ffmpeg.sh`
- Create: `desktop/ffmpeg-pins.lock`

**Interfaces:**
- Produces: `desktop/build/ffmpeg-out/ffmpeg`, `desktop/build/ffmpeg-out/ffprobe`, and `desktop/build/ffmpeg-macos-arm64-<ffmpeg>-<gpl|lgpl>.tar.gz` containing those two files, `BUILD-RECIPE.txt` (the configure line) and `SOURCES.txt` (every tarball URL + sha256). Task 1b publishes the tarball; Task 5 fetches it.

Build-host prerequisites (not shipped): `brew install nasm pkg-config meson ninja autoconf automake libtool`. The script checks for each and stops with the brew line if one is missing.

- [ ] **Step 1: Write the pins file**

`desktop/ffmpeg-pins.lock` is a four-column table the script reads: name, version, URL, sha256. The sha256 column starts as `-` and the script fills it on the first download (`--record-pins`), after which every later run verifies. Pick the newest stable release of each at implementation time (`ffmpeg -version` on the dev host is 9.0.1; use that unless a newer point release exists on ffmpeg.org/releases).

```
# name      version   url                                                                              sha256
ffmpeg      9.0.1     https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz                                  -
x264        stable    https://code.videolan.org/videolan/x264/-/archive/stable/x264-stable.tar.bz2    -
freetype    2.13.3    https://download.savannah.gnu.org/releases/freetype/freetype-2.13.3.tar.xz       -
harfbuzz    10.2.0    https://github.com/harfbuzz/harfbuzz/releases/download/10.2.0/harfbuzz-10.2.0.tar.xz -
fontconfig  2.15.0    https://www.freedesktop.org/software/fontconfig/release/fontconfig-2.15.0.tar.xz -
```

`x264-stable` is a moving snapshot; the recorded sha256 is what pins it. If the hash later mismatches, that is a real change upstream and the row's version column gets the commit id from the tarball's `version.sh`.

- [ ] **Step 2: Write `desktop/build-ffmpeg.sh`**

```bash
#!/usr/bin/env bash
# Build a static ffmpeg + ffprobe for macOS arm64 from pinned sources.
#
#   desktop/build-ffmpeg.sh               GPL build (libx264 + everything the pipeline uses)
#   desktop/build-ffmpeg.sh --lgpl        LGPL build (no --enable-gpl, no libx264)
#   desktop/build-ffmpeg.sh --record-pins fill empty sha256 cells in ffmpeg-pins.lock
#
# "Static" means: no dylib outside /usr/lib and /System/Library. zlib,
# bz2, lzma, iconv and expat come from the OS; everything else is linked in.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PINS="$HERE/ffmpeg-pins.lock"
BUILD="$HERE/build"
SRC="$BUILD/ffmpeg-src"
PREFIX="$BUILD/ffmpeg-prefix"
OUT="$BUILD/ffmpeg-out"
VARIANT=gpl
RECORD=0
for arg in "$@"; do
  case "$arg" in
    --lgpl) VARIANT=lgpl ;;
    --record-pins) RECORD=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

for tool in nasm pkg-config meson ninja autoconf automake glibtoolize; do
  command -v "$tool" >/dev/null || { echo "missing $tool: brew install nasm pkg-config meson ninja autoconf automake libtool" >&2; exit 1; }
done
[ "$(uname -m)" = arm64 ] || { echo "arm64 host required" >&2; exit 1; }

mkdir -p "$SRC" "$PREFIX" "$OUT"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export MACOSX_DEPLOYMENT_TARGET=12.0
JOBS="$(sysctl -n hw.ncpu)"

# --- pins -------------------------------------------------------------
pin() { awk -v n="$1" '$1==n {print $2, $3, $4}' "$PINS"; }
fetch() {  # name -> extracted dir path on stdout
  local name="$1" version url sha
  read -r version url sha < <(pin "$name")
  local tarball="$SRC/$(basename "$url")"
  [ -f "$tarball" ] || curl -fsSL -o "$tarball" "$url"
  local got; got="$(shasum -a 256 "$tarball" | cut -d' ' -f1)"
  if [ "$sha" = "-" ]; then
    [ "$RECORD" = 1 ] || { echo "$name has no sha256 in $PINS; run with --record-pins" >&2; exit 1; }
    awk -v n="$name" -v h="$got" 'BEGIN{OFS="\t"} $1==n {$4=h} {print}' "$PINS" > "$PINS.tmp" && mv "$PINS.tmp" "$PINS"
  elif [ "$sha" != "$got" ]; then
    echo "$name: sha256 mismatch (pinned $sha, got $got)" >&2; exit 1
  fi
  local dir="$SRC/$name-$version"
  if [ ! -d "$dir" ]; then
    mkdir -p "$dir"; tar -xf "$tarball" -C "$dir" --strip-components=1
  fi
  echo "$dir"
}

# --- deps -------------------------------------------------------------
build_freetype() {
  local d; d="$(fetch freetype)"; pushd "$d" >/dev/null
  ./configure --prefix="$PREFIX" --enable-static --disable-shared --with-harfbuzz=no --with-png=no --with-brotli=no --with-bzip2=yes --with-zlib=yes
  make -j"$JOBS" && make install; popd >/dev/null
}
build_harfbuzz() {
  local d; d="$(fetch harfbuzz)"; pushd "$d" >/dev/null
  meson setup build --prefix="$PREFIX" --default-library=static --buildtype=release \
    -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dchafa=disabled -Dicu=disabled -Dtests=disabled -Ddocs=disabled
  ninja -C build && ninja -C build install; popd >/dev/null
}
build_fontconfig() {
  local d; d="$(fetch fontconfig)"; pushd "$d" >/dev/null
  ./configure --prefix="$PREFIX" --enable-static --disable-shared --disable-docs --disable-tests --sysconfdir=/etc --localstatedir=/var
  make -j"$JOBS" && make install; popd >/dev/null
}
build_x264() {
  local d; d="$(fetch x264)"; pushd "$d" >/dev/null
  ./configure --prefix="$PREFIX" --enable-static --disable-shared --disable-cli --enable-pic
  make -j"$JOBS" && make install; popd >/dev/null
}

build_freetype; build_harfbuzz; build_fontconfig
[ "$VARIANT" = gpl ] && build_x264

# --- ffmpeg -----------------------------------------------------------
FFDIR="$(fetch ffmpeg)"
CONFIGURE=(
  ./configure --prefix="$PREFIX" --pkg-config-flags=--static
  --disable-shared --enable-static --disable-doc --disable-debug
  --disable-ffplay --enable-ffprobe
  --enable-videotoolbox --enable-audiotoolbox
  --enable-libfreetype --enable-libharfbuzz --enable-libfontconfig
  --extra-ldflags="-L$PREFIX/lib" --extra-cflags="-I$PREFIX/include"
)
if [ "$VARIANT" = gpl ]; then CONFIGURE+=(--enable-gpl --enable-libx264); fi
pushd "$FFDIR" >/dev/null
"${CONFIGURE[@]}"
make -j"$JOBS"
cp ffmpeg ffprobe "$OUT/"
popd >/dev/null

# --- verify and pack --------------------------------------------------
for bin in ffmpeg ffprobe; do
  if otool -L "$OUT/$bin" | tail -n +2 | grep -v -E '^\s+(/usr/lib/|/System/Library/)' ; then
    echo "$bin links a non-system dylib" >&2; exit 1
  fi
done
"$OUT/ffmpeg" -hide_banner -encoders | grep -q h264_videotoolbox
"$OUT/ffmpeg" -hide_banner -filters | grep -q drawtext
if [ "$VARIANT" = gpl ]; then "$OUT/ffmpeg" -hide_banner -encoders | grep -q libx264
else ! "$OUT/ffmpeg" -hide_banner -encoders | grep -q libx264; fi

FFVER="$(pin ffmpeg | cut -d' ' -f1)"
printf '%s\n' "${CONFIGURE[@]}" > "$OUT/BUILD-RECIPE.txt"
grep -v '^#' "$PINS" > "$OUT/SOURCES.txt"
tar -czf "$BUILD/ffmpeg-macos-arm64-$FFVER-$VARIANT.tar.gz" -C "$OUT" ffmpeg ffprobe BUILD-RECIPE.txt SOURCES.txt
echo "built $BUILD/ffmpeg-macos-arm64-$FFVER-$VARIANT.tar.gz"
```

- [ ] **Step 3: Run it once with `--record-pins`**

Run: `chmod +x desktop/build-ffmpeg.sh && desktop/build-ffmpeg.sh --record-pins`
Expected: 30 to 60 minutes; ends with `built desktop/build/ffmpeg-macos-arm64-9.0.1-gpl.tar.gz`; every sha256 cell in `ffmpeg-pins.lock` is filled. If a dep's configure flags do not match the pinned version (harfbuzz option names move between majors), fix the flag, not the pin.

- [ ] **Step 4: Verify the binary matches the pipeline's needs**

Run:
```bash
desktop/build/ffmpeg-out/ffmpeg -hide_banner -filters | grep -c -E ' (scale|pad|crop|overlay|xstack|split|fps|format|setpts|setsar|trim|tpad|concat|aresample|aformat|atrim|asetpts|asplit|adelay|amix|anullsrc|color|drawtext) '
desktop/build/ffmpeg-out/ffmpeg -hide_banner -encoders | grep -E 'h264_videotoolbox|hevc_videotoolbox|prores_ks|libx264| aac '
desktop/build/ffmpeg-out/ffmpeg -hide_banner -decoders | grep -E ' (h264|hevc|aac) '
```
Expected: 23 filters, all five encoders, all three decoders.

- [ ] **Step 5: Run the repo's ffmpeg-change check**

Per CLAUDE.md, a different ffmpeg build must re-render the fixture frames and diff. Run: `SPLITSMITH_FFMPEG=$PWD/desktop/build/ffmpeg-out/ffmpeg SPLITSMITH_FFPROBE=$PWD/desktop/build/ffmpeg-out/ffprobe uv run python scripts/render_match_frames.py --help` to find its output flag, then render the frames against the new binary and `compare` them with the committed ones (`scripts/render_match_frames.py` and `scripts/render_grid_frames.py`; look at the frames). Record any difference in the task's commit message; a pixel-identical result is the expected outcome for a build of the same FFmpeg version.

- [ ] **Step 6: Commit**

```bash
git add desktop/build-ffmpeg.sh desktop/ffmpeg-pins.lock
git commit -m "build(desktop): static ffmpeg build script with pinned sources"
```

### Task 1b: Publish the ffmpeg tarball and write the fetch script

**Files:**
- Create: `desktop/fetch-ffmpeg.sh`

**Interfaces:**
- Produces: `desktop/build/bin/ffmpeg`, `desktop/build/bin/ffprobe` (executable). Task 6's electron-builder config copies `build/bin` to `Contents/Resources/bin`.

- [ ] **Step 1: Create the GitHub release**

Run (from Task 1's output; adjust the version to the pinned one):
```bash
gh release create ffmpeg-macos-arm64-9.0.1-r1 \
  --title "ffmpeg macos arm64 9.0.1 (recipe r1)" \
  --notes "Static ffmpeg/ffprobe for the desktop app, built by desktop/build-ffmpeg.sh at the commit this tag points at. The GPL tarball links libx264. SOURCES.txt inside each tarball lists every source tarball and its sha256; this release is the complete corresponding source offer." \
  desktop/build/ffmpeg-macos-arm64-9.0.1-gpl.tar.gz \
  desktop/build/ffmpeg-src/*.tar.* 
```
The source tarballs are attached so the offer does not depend on upstream mirrors. Confirm with the user before creating the release (it is public and outward-facing).

- [ ] **Step 2: Write `desktop/fetch-ffmpeg.sh`**

```bash
#!/usr/bin/env bash
# Download the pinned ffmpeg tarball into desktop/build/bin, verifying sha256.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TAG="ffmpeg-macos-arm64-9.0.1-r1"
ASSET="ffmpeg-macos-arm64-9.0.1-gpl.tar.gz"
SHA256="<sha256 of the uploaded asset: shasum -a 256 desktop/build/ffmpeg-macos-arm64-9.0.1-gpl.tar.gz>"
URL="https://github.com/mandakan/splitsmith/releases/download/$TAG/$ASSET"
DEST="$HERE/build/bin"
mkdir -p "$DEST"
tarball="$HERE/build/$ASSET"
[ -f "$tarball" ] || curl -fsSL -o "$tarball" "$URL"
echo "$SHA256  $tarball" | shasum -a 256 -c -
tar -xzf "$tarball" -C "$DEST" ffmpeg ffprobe
chmod +x "$DEST/ffmpeg" "$DEST/ffprobe"
"$DEST/ffmpeg" -version | head -1
```
Fill `SHA256` with the real value before committing (the placeholder text above is replaced by the hash; the script must not ship with it). Check the GitHub owner/repo slug with `gh repo view --json nameWithOwner`.

- [ ] **Step 3: Verify**

Run: `rm -rf desktop/build/bin && chmod +x desktop/fetch-ffmpeg.sh && desktop/fetch-ffmpeg.sh`
Expected: prints `ffmpeg version 9.0.1 ...`; a wrong hash makes `shasum -c` fail with exit 1.

- [ ] **Step 4: Commit**

```bash
git add desktop/fetch-ffmpeg.sh
git commit -m "build(desktop): fetch the published static ffmpeg by sha256"
```

### Task 2: Runtime assembly script

**Files:**
- Create: `desktop/build-runtime.sh`

**Interfaces:**
- Consumes: a wheel at `dist/splitsmith-*.whl` (built by the caller; Task 7's `build.sh` does it).
- Produces: `desktop/build/runtime/python/` with `bin/python3.12`, `bin/splitsmith` and the wheel installed in its site-packages; relocatable.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Assemble the bundled Python runtime: python-build-standalone 3.12 + the
# splitsmith wheel installed into its own site-packages. No extras.
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
if "$PY" -c "import psycopg" 2>/dev/null; then echo "psycopg is installed; the bundle must not carry the hosted extra" >&2; exit 1; fi
"$PY" -c "import splitsmith.ui.server as s, sys; sys.exit(0 if s.STATIC_DIR.joinpath('index.html').exists() else 1)" \
  || { echo "wheel has no SPA dist" >&2; exit 1; }

# Precompile so the sealed bundle is never written to at runtime.
"$PY" -m compileall -q "$RUNTIME/python/lib/python$PYVER/site-packages" >/dev/null
# Strip the download cache and test dirs nobody runs.
find "$RUNTIME/python" -type d -name tests -path '*site-packages*' -prune -exec rm -rf {} +
rm -rf "$RUNTIME/python/lib/python$PYVER/test"
echo "runtime at $RUNTIME/python ($(du -sh "$RUNTIME/python" | cut -f1))"
```

If `uv pip install --python` refuses a non-venv target without `--system`, add `--system`; if `--break-system-packages` is rejected as unknown, drop it. Keep whichever pair the installed uv accepts and note it in the script's comment.

- [ ] **Step 2: Build a wheel and run the script**

Run:
```bash
(cd src/splitsmith/ui_static && pnpm install --frozen-lockfile && pnpm build && find dist -name '*.map' -delete)
uv build --wheel
chmod +x desktop/build-runtime.sh && desktop/build-runtime.sh
```
Expected: ends with `runtime at desktop/build/runtime/python (...)`. Note the size in the commit message.

- [ ] **Step 3: Verify relocatability and the embedded launch**

Run:
```bash
cp -R desktop/build/runtime/python "$TMPDIR/relocated-python"
SPLITSMITH_PORT=0 SPLITSMITH_CONFIG_DIR="$TMPDIR/cfg" "$TMPDIR/relocated-python/bin/python3.12" -m splitsmith.ui.embedded 2> "$TMPDIR/embedded.err" &
pid=$!; sleep 8; grep SPLITSMITH_READY "$TMPDIR/embedded.err"; kill $pid; wait $pid || true
"$TMPDIR/relocated-python/bin/splitsmith" --help | head -3
rm -rf "$TMPDIR/relocated-python"
```
Expected: a `SPLITSMITH_READY {...}` line with `"port": <nonzero>`; `--help` prints the CLI usage. If `bin/splitsmith` fails with a shebang pointing at the build path, rewrite the console-script shebangs in the script: `sed -i '' "1s|^#!.*|#!/bin/sh\n\"exec\" \"\$(dirname \"\$0\")/python3.12\" \"\$0\" \"\$@\"|" "$RUNTIME"/python/bin/splitsmith` (the two-line sh/python polyglot shebang) and re-verify.

- [ ] **Step 4: Confirm `.gitignore` covers the build output**

Run: `git status --porcelain desktop/ dist/ | head`
Expected: only `desktop/build-runtime.sh` is untracked; `desktop/build/` and `dist/` are ignored by the root rules.

- [ ] **Step 5: Commit**

```bash
git add desktop/build-runtime.sh
git commit -m "build(desktop): assemble the relocatable Python runtime from the wheel"
```

### Task 3: Electron project skeleton with pure sidecar logic and tests

**Files:**
- Create: `desktop/package.json`, `desktop/tsconfig.json`, `desktop/vitest.config.ts`
- Create: `desktop/src/sidecar.ts`, `desktop/src/sidecar.test.ts`

**Interfaces:**
- Produces (used by Task 4's `main.ts`):
  - `parseReadyLine(line: string): ReadyPayload | null`
  - `sidecarSpec(opts: SidecarOptions): SidecarSpec`
  - `isSidecarOrigin(url: string, baseUrl: string): boolean`

- [ ] **Step 1: Write `desktop/package.json`**

Run `pnpm view electron version` and `pnpm view electron-builder version` and pin the current majors with `^`.

```json
{
  "name": "splitsmith-desktop",
  "private": true,
  "version": "0.0.0",
  "description": "Electron shell around the splitsmith engine. The version is rewritten from pyproject.toml by build.sh; 0.0.0 is committed.",
  "main": "out/main.js",
  "packageManager": "pnpm@10.30.3",
  "scripts": {
    "compile": "tsc -p tsconfig.json",
    "start": "pnpm compile && electron .",
    "test": "vitest run",
    "typecheck": "tsc -p tsconfig.json --noEmit",
    "build": "./build.sh",
    "pack": "electron-builder --mac --arm64"
  },
  "devDependencies": {
    "@types/node": "^24.0.0",
    "electron": "^<current major>",
    "electron-builder": "^<current major>",
    "typescript": "^5.6.0",
    "vitest": "^3.0.0"
  }
}
```

- [ ] **Step 2: Write `desktop/tsconfig.json` and `desktop/vitest.config.ts`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "CommonJS",
    "moduleResolution": "Node",
    "strict": true,
    "esModuleInterop": true,
    "outDir": "out",
    "rootDir": "src",
    "types": ["node"],
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts"],
  "exclude": ["src/**/*.test.ts"]
}
```

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { include: ["src/**/*.test.ts"], environment: "node" },
});
```

- [ ] **Step 3: Write the failing tests**

```ts
// desktop/src/sidecar.test.ts
import { describe, expect, it } from "vitest";

import { isSidecarOrigin, parseReadyLine, sidecarSpec } from "./sidecar";

const READY =
  'SPLITSMITH_READY {"artifacts_dir": "/a", "base_url": "http://127.0.0.1:53241", "ffmpeg_binary": "/f", "host": "127.0.0.1", "log_file": null, "pid": 12, "port": 53241}';

describe("parseReadyLine", () => {
  it("parses the banner", () => {
    expect(parseReadyLine(READY)).toEqual({
      artifacts_dir: "/a",
      base_url: "http://127.0.0.1:53241",
      ffmpeg_binary: "/f",
      host: "127.0.0.1",
      log_file: null,
      pid: 12,
      port: 53241,
    });
  });
  it("ignores other lines and malformed banners", () => {
    expect(parseReadyLine("INFO uvicorn running")).toBeNull();
    expect(parseReadyLine("SPLITSMITH_READY {not json")).toBeNull();
    expect(parseReadyLine('SPLITSMITH_READY {"port": "x"}')).toBeNull();
  });
});

describe("sidecarSpec", () => {
  const spec = sidecarSpec({
    resourcesPath: "/App.app/Contents/Resources",
    port: 5000,
    home: "/Users/me",
    env: { PATH: "/usr/bin", PYTHONPATH: "/evil", PYTHONHOME: "/evil", HOME: "/Users/me" },
  });
  it("runs the bundled interpreter as the embedded module", () => {
    expect(spec.command).toBe("/App.app/Contents/Resources/python/bin/python3.12");
    expect(spec.args).toEqual(["-m", "splitsmith.ui.embedded", "--log-dir", "/Users/me/Library/Logs/Splitsmith"]);
  });
  it("points the engine at the bundled binaries and a writable numba cache", () => {
    expect(spec.env.SPLITSMITH_FFMPEG).toBe("/App.app/Contents/Resources/bin/ffmpeg");
    expect(spec.env.SPLITSMITH_FFPROBE).toBe("/App.app/Contents/Resources/bin/ffprobe");
    expect(spec.env.SPLITSMITH_PORT).toBe("5000");
    expect(spec.env.SPLITSMITH_HOST).toBe("127.0.0.1");
    expect(spec.env.NUMBA_CACHE_DIR).toBe("/Users/me/Library/Caches/Splitsmith/numba");
    expect(spec.env.PYTHONDONTWRITEBYTECODE).toBe("1");
    expect(spec.env.PYTHONNOUSERSITE).toBe("1");
  });
  it("drops inherited Python variables and never sets a project root", () => {
    expect(spec.env).not.toHaveProperty("PYTHONPATH");
    expect(spec.env).not.toHaveProperty("PYTHONHOME");
    expect(spec.env).not.toHaveProperty("SPLITSMITH_PROJECT_ROOT");
    expect(spec.env.PATH).toBe("/usr/bin");
  });
});

describe("isSidecarOrigin", () => {
  it("keeps the sidecar's pages in the window and sends the rest out", () => {
    expect(isSidecarOrigin("http://127.0.0.1:5000/results", "http://127.0.0.1:5000")).toBe(true);
    expect(isSidecarOrigin("http://127.0.0.1:5001/", "http://127.0.0.1:5000")).toBe(false);
    expect(isSidecarOrigin("https://my.splitsmith.app/share/x", "http://127.0.0.1:5000")).toBe(false);
    expect(isSidecarOrigin("not a url", "http://127.0.0.1:5000")).toBe(false);
  });
});
```

- [ ] **Step 4: Install and run the tests to see them fail**

Run: `cd desktop && pnpm install && pnpm test`
Expected: FAIL, `Cannot find module './sidecar'`.

- [ ] **Step 5: Write `desktop/src/sidecar.ts`**

```ts
/**
 * Pure helpers for launching the engine sidecar. No Electron imports, so
 * they run under vitest; main.ts wires them to the process.
 */
import path from "node:path";

export const READY_PREFIX = "SPLITSMITH_READY ";
export const PYTHON_RELATIVE = path.join("python", "bin", "python3.12");
export const CLI_RELATIVE = path.join("python", "bin", "splitsmith");

export interface ReadyPayload {
  host: string;
  port: number;
  pid: number;
  base_url: string;
  artifacts_dir: string;
  ffmpeg_binary: string;
  log_file: string | null;
}

export function parseReadyLine(line: string): ReadyPayload | null {
  if (!line.startsWith(READY_PREFIX)) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(line.slice(READY_PREFIX.length));
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const p = parsed as Record<string, unknown>;
  if (typeof p.port !== "number" || typeof p.base_url !== "string" || typeof p.host !== "string") return null;
  return {
    host: p.host,
    port: p.port,
    pid: typeof p.pid === "number" ? p.pid : -1,
    base_url: p.base_url,
    artifacts_dir: typeof p.artifacts_dir === "string" ? p.artifacts_dir : "",
    ffmpeg_binary: typeof p.ffmpeg_binary === "string" ? p.ffmpeg_binary : "",
    log_file: typeof p.log_file === "string" ? p.log_file : null,
  };
}

export interface SidecarOptions {
  resourcesPath: string;
  port: number;
  home: string;
  env: NodeJS.ProcessEnv;
}

export interface SidecarSpec {
  command: string;
  args: string[];
  env: Record<string, string>;
  logDir: string;
}

export function sidecarSpec({ resourcesPath, port, home, env }: SidecarOptions): SidecarSpec {
  const logDir = path.join(home, "Library", "Logs", "Splitsmith");
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined) continue;
    if (k.startsWith("PYTHON") || k.startsWith("SPLITSMITH_")) continue;
    clean[k] = v;
  }
  return {
    command: path.join(resourcesPath, PYTHON_RELATIVE),
    args: ["-m", "splitsmith.ui.embedded", "--log-dir", logDir],
    env: {
      ...clean,
      SPLITSMITH_HOST: "127.0.0.1",
      SPLITSMITH_PORT: String(port),
      SPLITSMITH_FFMPEG: path.join(resourcesPath, "bin", "ffmpeg"),
      SPLITSMITH_FFPROBE: path.join(resourcesPath, "bin", "ffprobe"),
      NUMBA_CACHE_DIR: path.join(home, "Library", "Caches", "Splitsmith", "numba"),
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONNOUSERSITE: "1",
    },
    logDir,
  };
}

export function isSidecarOrigin(url: string, baseUrl: string): boolean {
  try {
    return new URL(url).origin === new URL(baseUrl).origin;
  } catch {
    return false;
  }
}
```

- [ ] **Step 6: Run the tests**

Run: `cd desktop && pnpm test && pnpm typecheck`
Expected: 6 tests pass; typecheck clean.

- [ ] **Step 7: Commit**

```bash
git add desktop/package.json desktop/pnpm-lock.yaml desktop/tsconfig.json desktop/vitest.config.ts desktop/src/sidecar.ts desktop/src/sidecar.test.ts
git commit -m "feat(desktop): electron project skeleton with sidecar launch helpers"
```

### Task 4: Electron main process, loading page, preload

**Files:**
- Create: `desktop/src/main.ts`, `desktop/src/preload.ts`, `desktop/src/loading.html`

**Interfaces:**
- Consumes: `parseReadyLine`, `sidecarSpec`, `isSidecarOrigin` from Task 3.
- Produces: `sidecarState` object (`ready: ReadyPayload | null`) and `showFailure(lines: string[])` that Task 5's menu reuses; `engineVersion` read from `/api/health`.

- [ ] **Step 1: Write `desktop/src/loading.html`**

The page has two states toggled by a `data-state` attribute on `<body>`: `starting` and `failed`. Styling follows the SPA's dark surface (background `#0e0f11`, text `#e6e6e6`, the brand red `#e0332b` on the mark only), system font stack, no external assets.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Splitsmith</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #0e0f11; color: #e6e6e6; font: 14px/1.5 -apple-system, "Helvetica Neue", sans-serif;
         display: grid; place-items: center; height: 100vh; }
  main { width: min(560px, 90vw); }
  .mark { color: #e0332b; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: 12px; }
  h1 { font-size: 18px; font-weight: 500; margin: 8px 0 4px; }
  .muted { color: #8b8f96; }
  pre { background: #16181b; border: 1px solid #26292e; padding: 12px; border-radius: 4px; max-height: 40vh; overflow: auto; font-size: 12px; }
  button { background: transparent; color: #e6e6e6; border: 1px solid #3a3e45; border-radius: 4px; padding: 6px 12px; font: inherit; margin-right: 8px; cursor: pointer; }
  body[data-state="starting"] .failed, body[data-state="failed"] .starting { display: none; }
</style>
</head>
<body data-state="starting">
<main>
  <div class="mark">Splitsmith</div>
  <section class="starting">
    <h1>Starting engine</h1>
    <p class="muted">Version <span id="app-version"></span></p>
  </section>
  <section class="failed">
    <h1>The engine did not start</h1>
    <p class="muted">The last lines of its log:</p>
    <pre id="tail"></pre>
    <button id="open-log">Open log folder</button>
    <button id="quit">Quit</button>
  </section>
</main>
<script>
  document.getElementById("app-version").textContent = window.splitsmith?.appVersion ?? "";
  window.splitsmith?.onFailure((lines) => {
    document.getElementById("tail").textContent = lines.join("\n");
    document.body.dataset.state = "failed";
  });
  document.getElementById("open-log").addEventListener("click", () => window.splitsmith?.openLogFolder());
  document.getElementById("quit").addEventListener("click", () => window.splitsmith?.quit());
</script>
</body>
</html>
```

- [ ] **Step 2: Write `desktop/src/preload.ts`**

```ts
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("splitsmith", {
  appVersion: process.env.SPLITSMITH_APP_VERSION ?? "",
  onFailure: (cb: (lines: string[]) => void) => {
    ipcRenderer.on("sidecar-failed", (_e, lines: string[]) => cb(lines));
  },
  openLogFolder: () => ipcRenderer.send("open-log-folder"),
  quit: () => ipcRenderer.send("quit"),
});
```

The preload is attached only to the loading page's window load; once the SPA is loaded the same `BrowserWindow` keeps the preload, but the SPA never calls `window.splitsmith`, and the bridge exposes nothing that reaches the file system beyond opening the log folder.

- [ ] **Step 3: Write `desktop/src/main.ts`**

```ts
import { spawn, type ChildProcess } from "node:child_process";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";

import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";

import { buildMenu } from "./menu";
import { isSidecarOrigin, parseReadyLine, sidecarSpec, type ReadyPayload } from "./sidecar";

const READY_TIMEOUT_MS = 30_000;
const SHUTDOWN_GRACE_MS = 10_000;
const KILL_GRACE_MS = 5_000;
const TAIL_LINES = 50;

export const sidecarState: { ready: ReadyPayload | null; engineVersion: string; logDir: string } = {
  ready: null,
  engineVersion: "",
  logDir: path.join(os.homedir(), "Library", "Logs", "Splitsmith"),
};

let win: BrowserWindow | null = null;
let child: ChildProcess | null = null;
let quitting = false;
const tail: string[] = [];

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      srv.close(() => (typeof addr === "object" && addr ? resolve(addr.port) : reject(new Error("no port"))));
    });
    srv.on("error", reject);
  });
}

export function showFailure(lines: string[]): void {
  if (!win) return;
  const loading = path.join(__dirname, "..", "src", "loading.html");
  void win.loadFile(loading).then(() => win?.webContents.send("sidecar-failed", lines));
}

async function startSidecar(): Promise<void> {
  const port = await freePort();
  const spec = sidecarSpec({ resourcesPath: process.resourcesPath, port, home: os.homedir(), env: process.env });
  sidecarState.logDir = spec.logDir;
  child = spawn(spec.command, spec.args, { env: spec.env, stdio: ["ignore", "ignore", "pipe"] });
  const rl = readline.createInterface({ input: child.stderr! });
  let ready = false;
  const timer = setTimeout(() => {
    if (!ready) showFailure([`engine did not report ready within ${READY_TIMEOUT_MS / 1000} s`, ...tail]);
  }, READY_TIMEOUT_MS);
  rl.on("line", (line) => {
    tail.push(line);
    if (tail.length > TAIL_LINES) tail.shift();
    const payload = parseReadyLine(line);
    if (payload && !ready) {
      ready = true;
      clearTimeout(timer);
      sidecarState.ready = payload;
      void fetch(`${payload.base_url}/api/health`)
        .then((r) => r.json())
        .then((h: { version?: string }) => (sidecarState.engineVersion = h.version ?? ""))
        .catch(() => undefined)
        .finally(() => win?.loadURL(payload.base_url));
    }
  });
  child.on("exit", (code, signal) => {
    clearTimeout(timer);
    child = null;
    if (quitting) return;
    showFailure([`engine exited (code ${code ?? "null"}, signal ${signal ?? "none"})`, ...tail]);
  });
}

async function stopSidecar(): Promise<void> {
  const proc = child;
  if (!proc) return;
  const exited = new Promise<void>((resolve) => proc.once("exit", () => resolve()));
  if (sidecarState.ready) {
    await fetch(`${sidecarState.ready.base_url}/api/shutdown`, { method: "POST" }).catch(() => undefined);
  }
  const timeout = (ms: number) => new Promise<"timeout">((r) => setTimeout(() => r("timeout"), ms));
  if ((await Promise.race([exited, timeout(SHUTDOWN_GRACE_MS)])) === "timeout") {
    proc.kill("SIGTERM");
    if ((await Promise.race([exited, timeout(KILL_GRACE_MS)])) === "timeout") proc.kill("SIGKILL");
  }
}

function createWindow(): void {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    title: "Splitsmith",
    backgroundColor: "#0e0f11",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (sidecarState.ready && isSidecarOrigin(url, sidecarState.ready.base_url)) return { action: "allow" };
    void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith("file://")) return;
    if (sidecarState.ready && isSidecarOrigin(url, sidecarState.ready.base_url)) return;
    event.preventDefault();
    void shell.openExternal(url);
  });
  win.on("closed", () => (win = null));
  void win.loadFile(path.join(__dirname, "..", "src", "loading.html"));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
  process.env.SPLITSMITH_APP_VERSION = app.getVersion();
  app.whenReady().then(() => {
    buildMenu();
    createWindow();
    startSidecar().catch((err: Error) => showFailure([String(err.message)]));
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (event) => {
    if (quitting) return;
    quitting = true;
    event.preventDefault();
    void stopSidecar().finally(() => app.quit());
  });
  ipcMain.on("open-log-folder", () => void shell.openPath(sidecarState.logDir));
  ipcMain.on("quit", () => app.quit());
  process.on("uncaughtException", (err) => {
    dialog.showErrorBox("Splitsmith", err.message);
  });
}
```

`menu.ts` is Task 5; for this task create a stub `desktop/src/menu.ts` exporting `export function buildMenu(): void {}` so the compile passes, and replace it in Task 5.

- [ ] **Step 4: Run it unpackaged against the runtime**

`process.resourcesPath` under `electron .` points into `node_modules/electron/dist/...`, so add a dev override at the top of `startSidecar`: `const resourcesPath = process.env.SPLITSMITH_RESOURCES ?? process.resourcesPath;` and pass that to `sidecarSpec`. Then:

```bash
mkdir -p desktop/build/resources && ln -sfn ../runtime/python desktop/build/resources/python && ln -sfn ../bin desktop/build/resources/bin
cd desktop && SPLITSMITH_RESOURCES="$PWD/build/resources" pnpm start
```
Expected: the loading page for a few seconds, then the match picker in the window. Open a share link from the SPA: it opens in the default browser. Quit with Cmd-Q: the process list shows no leftover `python3.12` (`pgrep -fl splitsmith.ui.embedded` is empty).

- [ ] **Step 5: Exercise the failure page**

Run: `cd desktop && SPLITSMITH_RESOURCES=/nonexistent pnpm start`
Expected: the failure state with an ENOENT line, "Open log folder" opens Finder at `~/Library/Logs/Splitsmith`, Quit exits.

- [ ] **Step 6: Commit**

```bash
git add desktop/src/main.ts desktop/src/preload.ts desktop/src/loading.html desktop/src/menu.ts
git commit -m "feat(desktop): electron main process, loading and failure page"
```

### Task 5: Application menu with About, notices, CLI install and logs

**Files:**
- Create: `desktop/src/cliLink.ts`, `desktop/src/cliLink.test.ts`
- Modify: `desktop/src/menu.ts` (replace the stub)

**Interfaces:**
- Consumes: `sidecarState` and `showFailure` from `main.ts`; `CLI_RELATIVE` from `sidecar.ts`.
- Produces: `cliLinkPlan(source, target, existing): CliLinkPlan`; `buildMenu(): void`.

- [ ] **Step 1: Write the failing tests**

```ts
// desktop/src/cliLink.test.ts
import { describe, expect, it } from "vitest";

import { cliLinkPlan, CLI_TARGET } from "./cliLink";

const SRC = "/App.app/Contents/Resources/python/bin/splitsmith";

describe("cliLinkPlan", () => {
  it("links when nothing is there", () => {
    expect(cliLinkPlan(SRC, CLI_TARGET, null)).toEqual({ kind: "link", source: SRC, target: "/usr/local/bin/splitsmith" });
  });
  it("is a no-op when the link already points at this app", () => {
    expect(cliLinkPlan(SRC, CLI_TARGET, SRC)).toEqual({ kind: "already", target: "/usr/local/bin/splitsmith" });
  });
  it("relinks when the link points at an older copy of the app", () => {
    expect(cliLinkPlan(SRC, CLI_TARGET, "/old/Splitsmith.app/Contents/Resources/python/bin/splitsmith")).toEqual({
      kind: "link", source: SRC, target: "/usr/local/bin/splitsmith",
    });
  });
  it("refuses to replace something that is not ours", () => {
    expect(cliLinkPlan(SRC, CLI_TARGET, "/opt/homebrew/bin/splitsmith")).toEqual({
      kind: "conflict", target: "/usr/local/bin/splitsmith", existing: "/opt/homebrew/bin/splitsmith",
    });
  });
});
```

- [ ] **Step 2: Run to see it fail**

Run: `cd desktop && pnpm test`
Expected: FAIL, `Cannot find module './cliLink'`.

- [ ] **Step 3: Write `desktop/src/cliLink.ts`**

```ts
/** Plan for "Install command line tool": what to do at /usr/local/bin/splitsmith. */
export const CLI_TARGET = "/usr/local/bin/splitsmith";

export type CliLinkPlan =
  | { kind: "already"; target: string }
  | { kind: "link"; source: string; target: string }
  | { kind: "conflict"; target: string; existing: string };

const OURS = /\.app\/Contents\/Resources\/python\/bin\/splitsmith$/;

/** `existing` is what the target currently resolves to (a symlink's value,
 *  a plain file's own path), or null when nothing is there. */
export function cliLinkPlan(source: string, target: string, existing: string | null): CliLinkPlan {
  if (existing === null) return { kind: "link", source, target };
  if (existing === source) return { kind: "already", target };
  if (OURS.test(existing)) return { kind: "link", source, target };
  return { kind: "conflict", target, existing };
}
```

- [ ] **Step 4: Run the tests**

Run: `cd desktop && pnpm test`
Expected: all pass (6 + 4).

- [ ] **Step 5: Write `desktop/src/menu.ts`**

```ts
import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { app, BrowserWindow, dialog, Menu, shell } from "electron";

import { cliLinkPlan, CLI_TARGET } from "./cliLink";
import { sidecarState } from "./main";
import { CLI_RELATIVE } from "./sidecar";

function resourcesPath(): string {
  return process.env.SPLITSMITH_RESOURCES ?? process.resourcesPath;
}

function showAbout(): void {
  void dialog.showMessageBox({
    type: "info",
    title: "About Splitsmith",
    message: "Splitsmith",
    detail: `App ${app.getVersion()}\nEngine ${sidecarState.engineVersion || "not started"}`,
    buttons: ["Third-party notices", "OK"],
    defaultId: 1,
  }).then(({ response }) => {
    if (response === 0) showNotices();
  });
}

function showNotices(): void {
  const file = path.join(resourcesPath(), "NOTICES.md");
  const w = new BrowserWindow({ width: 720, height: 800, title: "Third-party notices", backgroundColor: "#0e0f11" });
  const text = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "NOTICES.md is missing from this build.";
  const html = `<!doctype html><meta charset="utf-8"><style>body{background:#0e0f11;color:#e6e6e6;font:13px/1.5 -apple-system,sans-serif;padding:24px}pre{white-space:pre-wrap}</style><pre>${text.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!)}</pre>`;
  void w.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
}

function currentTarget(target: string): string | null {
  try {
    const st = fs.lstatSync(target);
    return st.isSymbolicLink() ? fs.readlinkSync(target) : target;
  } catch {
    return null;
  }
}

function installCli(): void {
  const source = path.join(resourcesPath(), CLI_RELATIVE);
  const plan = cliLinkPlan(source, CLI_TARGET, currentTarget(CLI_TARGET));
  if (plan.kind === "already") {
    void dialog.showMessageBox({ message: "Already installed", detail: `${CLI_TARGET} points at this app.` });
    return;
  }
  if (plan.kind === "conflict") {
    void dialog.showMessageBox({ type: "warning", message: "Not installed", detail: `${CLI_TARGET} is ${plan.existing}, which is not this app. Remove it first.` });
    return;
  }
  try {
    fs.mkdirSync(path.dirname(plan.target), { recursive: true });
    try { fs.unlinkSync(plan.target); } catch { /* nothing there */ }
    fs.symlinkSync(plan.source, plan.target);
    void dialog.showMessageBox({ message: "Installed", detail: `Run 'splitsmith' in a terminal.` });
  } catch {
    const cmd = `mkdir -p '${path.dirname(plan.target)}' && ln -sfn '${plan.source}' '${plan.target}'`;
    execFile("osascript", ["-e", `do shell script "${cmd.replace(/"/g, '\\"')}" with administrator privileges`], (err) => {
      if (err) void dialog.showMessageBox({ type: "error", message: "Not installed", detail: err.message });
      else void dialog.showMessageBox({ message: "Installed", detail: `Run 'splitsmith' in a terminal.` });
    });
  }
}

export function buildMenu(): void {
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: app.name,
      submenu: [
        { label: "About Splitsmith", click: showAbout },
        { label: "Third-party notices", click: showNotices },
        { type: "separator" },
        { label: "Install command line tool", click: installCli },
        { type: "separator" },
        { role: "hide" }, { role: "hideOthers" }, { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { label: "File", submenu: [{ label: "Open log folder", click: () => void shell.openPath(sidecarState.logDir) }, { role: "close" }] },
    { role: "editMenu" },
    { label: "View", submenu: [{ role: "reload" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }, { role: "toggleDevTools" }] },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
```

`main.ts` imports `menu.ts` and `menu.ts` imports `sidecarState` from `main.ts`; that circular import is fine under CommonJS because `sidecarState` is read at click time, not at module load. If `tsc` or the runtime complains, move `sidecarState` into `sidecar.ts` (it has no Electron dependency) and import it from there in both files.

- [ ] **Step 6: Verify by hand**

Run: `cd desktop && SPLITSMITH_RESOURCES="$PWD/build/resources" pnpm start`, then: About shows both versions; Third-party notices opens a window (says NOTICES.md is missing, expected until Task 7); Install command line tool creates `/usr/local/bin/splitsmith` (or prompts for admin) and `splitsmith --help` works in a terminal; a second "Install" says Already installed. Then `rm /usr/local/bin/splitsmith` if you do not want the dev build linked.

- [ ] **Step 7: Commit**

```bash
git add desktop/src/cliLink.ts desktop/src/cliLink.test.ts desktop/src/menu.ts desktop/src/main.ts
git commit -m "feat(desktop): about, notices, install command line tool, open logs"
```

### Task 6: electron-builder config, entitlements, build pipeline

**Files:**
- Create: `desktop/electron-builder.yml`, `desktop/entitlements.plist`, `desktop/build.sh`, `desktop/verify-signed.sh`

**Interfaces:**
- Consumes: `build/runtime/python`, `build/bin`, `build/NOTICES.md` (Task 7 produces the notices; until then `build.sh` writes a one-line placeholder file so packaging works, and Task 7 replaces that step).
- Produces: `desktop/dist/Splitsmith-<ver>-arm64.dmg` and `desktop/dist/mac-arm64/Splitsmith.app`.

- [ ] **Step 1: Write `desktop/entitlements.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key>
  <true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
  <true/>
</dict>
</plist>
```

- [ ] **Step 2: Write `desktop/electron-builder.yml`**

```yaml
appId: se.thias.splitsmith
productName: Splitsmith
copyright: Copyright (c) Mathias Axell
directories:
  output: dist
  buildResources: build-resources
files:
  - out/**
  - src/loading.html
  - package.json
extraResources:
  - from: build/runtime/python
    to: python
  - from: build/bin
    to: bin
  - from: build/NOTICES.md
    to: NOTICES.md
asar: true
mac:
  target:
    - target: dmg
      arch: [arm64]
  category: public.app-category.video
  hardenedRuntime: true
  gatekeeperAssess: false
  entitlements: entitlements.plist
  entitlementsInherit: entitlements.plist
  notarize: true
  darkModeSupport: true
  extendInfo:
    LSMinimumSystemVersion: "12.0"
dmg:
  artifactName: ${productName}-${version}-${arch}.${ext}
  contents:
    - x: 130
      y: 220
    - x: 410
      y: 220
      type: link
      path: /Applications
    - x: 270
      y: 380
      type: file
      path: build/NOTICES.md
```

`notarize: true` reads `APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER` from the environment; with `CSC_IDENTITY_AUTO_DISCOVERY=false` electron-builder skips both signing and notarization. An app icon is out of scope for this round; electron-builder uses Electron's default (add `build-resources/icon.icns` later).

- [ ] **Step 3: Write `desktop/build.sh`**

```bash
#!/usr/bin/env bash
# The desktop build pipeline (spec: "Build pipeline"). Run from anywhere.
#   CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh   -> unsigned, fast
#   desktop/build.sh                                     -> signed + notarized (needs the cert and APPLE_API_* env)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

echo "== 1/6 SPA"
(cd "$ROOT/src/splitsmith/ui_static" && pnpm install --frozen-lockfile && pnpm build && find dist -name '*.map' -delete)
echo "== 2/6 wheel"
(cd "$ROOT" && rm -rf dist && uv build --wheel)
echo "== 3/6 runtime"
"$HERE/build-runtime.sh"
echo "== 4/6 ffmpeg"
"$HERE/fetch-ffmpeg.sh"
echo "== 5/6 notices"
"$HERE/build-notices.sh"
echo "== 6/6 electron-builder"
version="$(cd "$ROOT" && uv run --no-project python -c "import tomllib,sys; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
(cd "$HERE" && pnpm install --frozen-lockfile && pnpm compile && npm pkg set version="$version" && pnpm exec electron-builder --mac --arm64; git checkout -- package.json)
if [ "${CSC_IDENTITY_AUTO_DISCOVERY:-true}" != "false" ]; then
  "$HERE/verify-signed.sh" "$HERE/dist/mac-arm64/Splitsmith.app"
fi
ls -la "$HERE"/dist/*.dmg
```

Until Task 7 exists, create `desktop/build-notices.sh` as a stub that writes `Notices are generated in a later task.` to `build/NOTICES.md`; Task 7 replaces it.

- [ ] **Step 4: Write `desktop/verify-signed.sh`**

```bash
#!/usr/bin/env bash
# Post-sign checks. Fails on the first problem.
set -euo pipefail
APP="${1:?path to .app}"
codesign --verify --deep --strict --verbose=2 "$APP"
spctl --assess --type execute --verbose=2 "$APP"
xcrun stapler validate "$APP"
codesign -d --entitlements - "$APP" | grep -q allow-unsigned-executable-memory
echo "signed, notarized, stapled: $APP"
```

- [ ] **Step 5: Unsigned build**

Run: `chmod +x desktop/*.sh && CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh`
Expected: `desktop/dist/Splitsmith-<ver>-arm64.dmg` listed; `desktop/dist/mac-arm64/Splitsmith.app/Contents/Resources/python/bin/python3.12` and `Resources/bin/ffmpeg` exist. Then `open desktop/dist/mac-arm64/Splitsmith.app`: the picker appears. Note the DMG size in the commit message.

- [ ] **Step 6: Signed build (needs the certificate)**

Prerequisite: a Developer ID Application certificate in the login keychain (`security find-identity -v -p codesigning | grep "Developer ID Application"` prints one line). Ask the user to create it if missing (Xcode > Settings > Accounts > Manage Certificates > + > Developer ID Application). Export the ASC API key env from the user's existing setup (`APPLE_API_KEY` path to the `.p8`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER`).

Run: `desktop/build.sh`
Expected: notarization takes 2 to 10 minutes; `verify-signed.sh` prints `signed, notarized, stapled`. Mount the DMG, drag to `/Applications`, launch from Finder with no Gatekeeper prompt beyond the first-open confirmation.

- [ ] **Step 7: Commit**

```bash
git add desktop/electron-builder.yml desktop/entitlements.plist desktop/build.sh desktop/verify-signed.sh desktop/build-notices.sh
git commit -m "build(desktop): electron-builder config, entitlements, signed build pipeline"
```

### Task 7: Third-party notices

**Files:**
- Create: `desktop/NOTICES.head.md`
- Modify: `desktop/build-notices.sh` (replace the stub)

**Interfaces:**
- Produces: `desktop/build/NOTICES.md` consumed by Task 6's electron-builder config and the About panel.

- [ ] **Step 1: Verify the model weight licenses**

Run: `uv run python -c "from splitsmith.models.registry import *; import splitsmith.models.registry as r; print([(a.name, getattr(a,'source',None)) for a in r.ARTIFACTS])"` (adapt to the registry's real names; the point is to list which HF repos the ONNX graphs came from). Then read the license fields on `https://huggingface.co/laion/clap-htsat-unfused` and the PANNs CNN14 source (`https://github.com/qiuqiangkong/audioset_tagging_cnn`, MIT) and write what they say into `NOTICES.head.md` verbatim with the URL and the date checked. This closes #986's "verify model weight licenses" item; say so in the commit.

- [ ] **Step 2: Write `desktop/NOTICES.head.md`**

```markdown
# Third-party notices

Splitsmith bundles the software below. Each is the property of its authors
and used under the license named.

## FFmpeg (ffmpeg, ffprobe)

GNU General Public License, version 3 or later. This build enables libx264,
which is GPL. The complete corresponding source, the build recipe and every
input tarball are published at
https://github.com/mandakan/splitsmith/releases/tag/ffmpeg-macos-arm64-9.0.1-r1
and the recipe is desktop/build-ffmpeg.sh in the same repository. FFmpeg
runs as a separate executable; Splitsmith itself is MIT licensed.

## Electron

MIT License. Copyright (c) Electron contributors, OpenJS Foundation.

## Fonts

Antonio, JetBrains Mono and Geist: SIL Open Font License 1.1.

## Model weights

- CLAP (laion/clap-htsat-unfused), converted to ONNX: <license as stated on the model card, checked YYYY-MM-DD>.
- PANNs CNN14 (qiuqiangkong/audioset_tagging_cnn), converted to ONNX: MIT (checked YYYY-MM-DD).

## Python packages

Generated by pip-licenses over the bundled runtime:
```

Replace the two `<...>` and `YYYY-MM-DD` with what Step 1 found before committing.

- [ ] **Step 3: Write `desktop/build-notices.sh`**

```bash
#!/usr/bin/env bash
# Generate build/NOTICES.md: the hand-written head + both dependency trees.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="$HERE/build/runtime/python/bin/python3.12"
OUT="$HERE/build/NOTICES.md"
[ -x "$PY" ] || { echo "runtime missing; run build-runtime.sh" >&2; exit 1; }
{
  cat "$HERE/NOTICES.head.md"
  echo
  uvx --python "$PY" pip-licenses --python "$PY" --format=markdown --with-authors --order=name
  echo
  echo "## JavaScript packages (SPA)"
  echo
  (cd "$ROOT/src/splitsmith/ui_static" && pnpm exec license-checker --production --summary 2>/dev/null || pnpm dlx license-checker --production --summary)
} > "$OUT"
echo "wrote $OUT ($(wc -l < "$OUT") lines)"
```

If `uvx --python "$PY" pip-licenses` cannot run against a non-venv interpreter, use `uv pip install --python "$PY" --break-system-packages pip-licenses` inside `build-runtime.sh` temporarily and uninstall it after generating (`uv pip uninstall --python "$PY" pip-licenses`), so the shipped runtime does not carry it.

- [ ] **Step 4: Run and read the output**

Run: `desktop/build-notices.sh && head -60 desktop/build/NOTICES.md && grep -c '|' desktop/build/NOTICES.md`
Expected: the head, then a markdown table with one row per installed package (roughly 80 rows), then the license-checker summary. Check that no row says `UNKNOWN`; if one does, look the package up and add a line for it under the head.

- [ ] **Step 5: Rebuild unsigned and check the About panel**

Run: `CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh && open desktop/dist/mac-arm64/Splitsmith.app`, then Splitsmith > Third-party notices.
Expected: the notices window shows the generated file.

- [ ] **Step 6: Commit**

```bash
git add desktop/NOTICES.head.md desktop/build-notices.sh
git commit -m "build(desktop): generated third-party notices; model weight licenses verified (#986)"
```

### Task 8: Engine routes for the Chromium install

**Files:**
- Create: `src/splitsmith/ui/system_api.py`
- Create: `tests/test_system_api.py`
- Modify: `src/splitsmith/ui/server.py` (include the router next to `app.include_router(youtube_router)`, around line 16636; register the job body next to `state.jobs.bodies.register("model_download", ...)` around line 4366)

**Interfaces:**
- Produces: `GET /api/system/chromium` -> `ChromiumStatus {installed: bool, channel: str}`; `POST /api/system/chromium/install` -> the `Job` snapshot (202); job kind `chromium_install`. Hosted mode: both 404.
- `system_api.rasterizer_factory` module attribute for tests, mirroring `export_preview_api.rasterizer_factory`.

- [ ] **Step 1: Write the failing tests**

```python
"""``/api/system/chromium`` routes (desktop packaging spec, "First-run downloads")."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from splitsmith.overlay_raster import RasterizerUnavailableError
from splitsmith.ui import system_api
from splitsmith.ui.server import create_app

STATUS = "/api/system/chromium"
INSTALL = "/api/system/chromium/install"


@contextmanager
def _browser_present():
    yield object()


@contextmanager
def _browser_missing():
    raise RasterizerUnavailableError("no browser", "install one")
    yield  # pragma: no cover


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    app = create_app(project_root=None, project_name=None)
    return TestClient(app)


def test_status_reports_installed_when_the_rasterizer_launches(client, monkeypatch) -> None:
    monkeypatch.setattr(system_api, "rasterizer_factory", _browser_present)
    r = client.get(STATUS)
    assert r.status_code == 200
    assert r.json() == {"installed": True, "channel": "chromium-headless-shell"}


def test_status_reports_missing_when_it_cannot(client, monkeypatch) -> None:
    monkeypatch.setattr(system_api, "rasterizer_factory", _browser_missing)
    assert client.get(STATUS).json()["installed"] is False


def test_install_runs_playwright_with_the_bundled_interpreter(client, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], handle: Any) -> int:
        calls.append(argv)
        handle.update(progress=1.0, message="done")
        return 0

    monkeypatch.setattr(system_api, "_run_install", fake_run)
    r = client.post(INSTALL)
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    for _ in range(50):
        job = client.get(f"/api/me/jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed"}:
            break
    assert job["status"] == "succeeded", job
    assert calls == [[sys.executable, "-m", "playwright", "install", "chromium-headless-shell"]]


def test_install_is_idempotent_while_running(client, monkeypatch) -> None:
    import threading

    gate = threading.Event()

    def slow_run(argv: list[str], handle: Any) -> int:
        gate.wait(5)
        return 0

    monkeypatch.setattr(system_api, "_run_install", slow_run)
    first = client.post(INSTALL).json()
    second = client.post(INSTALL).json()
    gate.set()
    assert second["id"] == first["id"]


def test_routes_are_404_hosted(monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.setattr(system_api, "_hosted", lambda: True)
    app = create_app(project_root=None, project_name=None)
    c = TestClient(app)
    assert c.get(STATUS).status_code == 404
    assert c.post(INSTALL).status_code == 404
```

Check `create_app`'s real signature (`grep -n "def create_app" src/splitsmith/ui/server.py`) and how `tests/test_export_preview_api.py`'s `_seed_match_export_project` builds a client; use the same construction if `create_app(project_root=None, ...)` is not how the other tests do it. For the hosted test, if `create_app` with `SPLITSMITH_MODE=hosted` needs a database, do not set the env; monkeypatching `system_api._hosted` is enough since the router only consults that function.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_system_api.py -n0 -v`
Expected: FAIL, `cannot import name 'system_api'`.

- [ ] **Step 3: Write `src/splitsmith/ui/system_api.py`**

```python
"""Local-only system routes: is Playwright's Chromium installed, and install it.

The desktop app has no terminal, so ``overlay_raster.INSTALL_HINT`` is
not actionable there. ``GET /api/system/chromium`` answers whether the
rasterizer can launch (the same probe a render performs) and
``POST /api/system/chromium/install`` runs ``playwright install`` for the
headless-shell channel as a job, so the SPA follows it on the progress
strip. Hosted containers preinstall the browser and these routes 404
there. This module must not import ``server``; it reaches state through
``request.app.state``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..overlay_raster import CHROMIUM_CHANNEL, ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from .jobs import Job, JobHandle, JobStatus

router = APIRouter()

JOB_KIND = "chromium_install"

#: Test seam, same shape as ``export_preview_api.rasterizer_factory``.
rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]] = ChromiumRasterizer


class ChromiumStatus(BaseModel):
    installed: bool
    channel: str = CHROMIUM_CHANNEL


def _hosted() -> bool:
    return os.environ.get("SPLITSMITH_MODE") == "hosted"


def _local_only() -> None:
    if _hosted():
        raise HTTPException(status_code=404, detail="not found")


def _probe() -> bool:
    try:
        with rasterizer_factory():
            return True
    except RasterizerUnavailableError:
        return False


def _run_install(argv: list[str], handle: JobHandle) -> int:
    """Run ``argv`` streaming its output lines into the job message. Test seam."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    handle.attach_subprocess(proc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            handle.check_cancel()
            text = line.strip()
            if text:
                handle.update(message=text[:200])
        return proc.wait()
    finally:
        handle.detach_subprocess()


def run_chromium_install(handle: JobHandle) -> None:
    """Job body for ``chromium_install``."""
    argv = [sys.executable, "-m", "playwright", "install", CHROMIUM_CHANNEL]
    handle.update(progress=0.0, message="Downloading Chromium headless shell")
    code = _run_install(argv, handle)
    if code != 0:
        raise RuntimeError(f"playwright install exited {code}")
    handle.update(progress=1.0, message="Chromium installed")


def _state(request: Request) -> Any:
    return request.app.state.splitsmith_state


@router.get("/api/system/chromium", response_model=ChromiumStatus)
async def chromium_status(request: Request) -> ChromiumStatus:
    _local_only()
    from starlette.concurrency import run_in_threadpool

    return ChromiumStatus(installed=await run_in_threadpool(_probe))


@router.post("/api/system/chromium/install", response_model=Job, status_code=202)
async def chromium_install(request: Request) -> Job:
    _local_only()
    jobs = _state(request).jobs
    for job in jobs.list():
        if job.kind == JOB_KIND and job.status in {JobStatus.PENDING, JobStatus.RUNNING}:
            return job
    return await jobs.submit(kind=JOB_KIND)
```

Check the real names before relying on them: `JobStatus` members (`grep -n -A12 "class JobStatus" src/splitsmith/ui/jobs.py`) and the registry's list method (`grep -n "def list\|def snapshot\|def all" src/splitsmith/ui/jobs.py`); the `/api/me/jobs` handler at `server.py:10606` shows which one the server itself uses.

- [ ] **Step 4: Wire it into `server.py`**

Next to `state.jobs.bodies.register("model_download", _run_model_download_job)`:
```python
    state.jobs.bodies.register(system_api.JOB_KIND, system_api.run_chromium_install)
```
Next to `app.include_router(youtube_router)`:
```python
    app.include_router(system_api.router)
```
With `from . import system_api` in the import block that already brings in the other `_api` modules.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_system_api.py tests/test_export_preview_api.py -n0 -v`
Expected: all pass. Then `uv run ruff check src/splitsmith/ui/system_api.py tests/test_system_api.py && uv run black --check src/splitsmith/ui/system_api.py tests/test_system_api.py`.

- [ ] **Step 6: Prove one test fails against the pre-change code**

Comment out the `include_router` line, run `uv run pytest tests/test_system_api.py -n0 -q`; expected: the four local-mode tests fail with 404. Restore the line.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui/system_api.py src/splitsmith/ui/server.py tests/test_system_api.py
git commit -m "feat(ui): local-only routes to probe and install Playwright's Chromium"
```

### Task 9: SPA: "Install renderer" under the preview's browser line

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (add `getChromiumStatus`, `installChromium` next to `getServerFeatures` at line 3172)
- Modify: `src/splitsmith/ui_static/src/components/export/PreviewPane.tsx`, `PreviewPane.test.tsx`

**Interfaces:**
- Consumes: `GET /api/system/chromium`, `POST /api/system/chromium/install` from Task 8; `useDeploymentMode` from `lib/features.ts`; `Button` from `components/ui/Button`.

- [ ] **Step 1: Add the client calls**

```ts
  getChromiumStatus: () => request<{ installed: boolean; channel: string }>("/api/system/chromium"),
  installChromium: () => request<Job>("/api/system/chromium/install", { method: "POST" }),
```
Match how other POSTs in `api.ts` pass the method (look at `cancelJob` near `listJobs`, line 3614).

- [ ] **Step 2: Write the failing test**

Add to `PreviewPane.test.tsx`, next to the 503 test. The file already mocks `api`; extend the mock with `installChromium` and mock `useDeploymentMode` to return `{ mode: "local", resolved: true }` (check `features.ts:79` for the exact shape).

```tsx
  it("offers to install the renderer when the preview needs a browser, locally", async () => {
    const OVERLAY = { slotId: "overlay" as const, variantId: "on" };
    vi.mocked(api.exportPreview).mockRejectedValue(new ApiError(503, "no browser"));
    vi.mocked(api.installChromium).mockResolvedValue({ id: "j1", kind: "chromium_install", status: "pending" } as never);
    render(
      <PreviewPane slug="me" stageNumber={3} settings={settings} projectName="Bromma" focus={OVERLAY} hover={null} enabled />,
    );
    await settle();
    const button = screen.getByRole("button", { name: "Install renderer (260 MB)" });
    await userEvent.click(button);
    expect(api.installChromium).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Installing renderer")).toBeInTheDocument();
  });
```

If the file does not use `userEvent`, use `fireEvent.click` from `@testing-library/react` as the neighbouring tests do.

- [ ] **Step 3: Run to see it fail**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/export/PreviewPane.test.tsx`
Expected: FAIL, no button with that name.

- [ ] **Step 4: Implement in `PreviewPane.tsx`**

Below the `previewLine` paragraph (line 102-104):

```tsx
      {failed && status === 503 && mode === "local" ? (
        <div className="px-3.5 pb-2">
          {installing ? (
            <p className="text-sm text-muted">Installing renderer</p>
          ) : (
            <Button variant="default" size="sm" onClick={startInstall}>
              Install renderer (260 MB)
            </Button>
          )}
        </div>
      ) : null}
```

with, in the component body:

```tsx
  const { mode } = useDeploymentMode();
  const [installing, setInstalling] = useState(false);
  const startInstall = () => {
    setInstalling(true);
    api
      .installChromium()
      .then(() => setInstalling(true))
      .catch(() => setInstalling(false));
  };
```

The job then appears on the progress strip like any other; when it finishes the next preview request (any settings change, or the strip's completion re-render) succeeds. To retry the preview without a user edit, subscribe the way the strip does: if `lib/jobs` exposes a `useJobs`/`onJobFinished` hook, call it and bump a `retry` counter that is part of the effect's dependency list; otherwise leave the retry to the next edit and say so in the commit. Import `Button` from `@/components/ui/Button` and `useDeploymentMode` from `@/lib/features`. Check `Button`'s `size` prop values in `components/ui/Button.tsx:16-34` and use an existing one.

- [ ] **Step 5: Run the SPA checks**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/export && pnpm lint && pnpm typecheck`
Expected: all pass; the lint rule for arbitrary sizes is not triggered (no `text-[...]`).

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/components/export/PreviewPane.tsx src/splitsmith/ui_static/src/components/export/PreviewPane.test.tsx
git commit -m "feat(export): install the overlay renderer from the preview pane in local mode"
```

### Task 10: Smoke test and CI job

**Files:**
- Create: `desktop/smoke.sh`
- Create: `.github/workflows/desktop.yml`

**Interfaces:**
- Consumes: a built `.app` (Task 6).

- [ ] **Step 1: Write `desktop/smoke.sh`**

```bash
#!/usr/bin/env bash
# Exercise the built app's sidecar the way the shell does. Needs network the
# first time (model download into the temp config dir).
set -euo pipefail
APP="${1:-$(dirname "$0")/dist/mac-arm64/Splitsmith.app}"
RES="$APP/Contents/Resources"
PY="$RES/python/bin/python3.12"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$(mktemp -d)"
MODELS="${SPLITSMITH_SMOKE_MODELS:-$HOME/.splitsmith/models}"
trap 'kill "${pid:-}" 2>/dev/null || true; rm -rf "$CFG"' EXIT

[ -x "$PY" ] || { echo "no interpreter at $PY" >&2; exit 1; }
[ -x "$RES/bin/ffmpeg" ] || { echo "no ffmpeg in bundle" >&2; exit 1; }
# Reuse a model cache when one is available so the smoke run is offline.
if [ -d "$MODELS" ]; then mkdir -p "$CFG" && ln -s "$MODELS" "$CFG/models"; fi

export SPLITSMITH_CONFIG_DIR="$CFG" SPLITSMITH_PORT=0 SPLITSMITH_HOST=127.0.0.1
export SPLITSMITH_FFMPEG="$RES/bin/ffmpeg" SPLITSMITH_FFPROBE="$RES/bin/ffprobe"
export NUMBA_CACHE_DIR="$CFG/numba" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
unset PYTHONPATH PYTHONHOME

"$PY" -m splitsmith.ui.embedded --log-dir "$CFG/logs" 2> "$CFG/stderr" &
pid=$!
for _ in $(seq 1 60); do grep -q SPLITSMITH_READY "$CFG/stderr" && break; sleep 0.5; done
banner="$(grep SPLITSMITH_READY "$CFG/stderr" | head -1 | cut -d' ' -f2-)"
[ -n "$banner" ] || { echo "no READY banner"; cat "$CFG/stderr"; exit 1; }
base="$(printf '%s' "$banner" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["base_url"])')"
ffm="$(printf '%s' "$banner" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["ffmpeg_binary"])')"
[ "$ffm" = "$RES/bin/ffmpeg" ] || { echo "sidecar resolved ffmpeg to $ffm, not the bundle"; exit 1; }

curl -fsS "$base/api/health" | grep -q '"status":"ok"'
"$RES/bin/ffmpeg" -hide_banner -encoders | grep -q h264_videotoolbox

# Detection through the bundled CLI (same fixture and range as ci.yml's slim-smoke).
"$RES/python/bin/splitsmith" detect \
  --video "$ROOT/tests/fixtures/stage-shots-tallmilan-2026-stage3-s97dcec94.wav" --time 14.74 | tee "$CFG/detect.txt"
count=$(grep -oE '[0-9]+ shots' "$CFG/detect.txt" | head -1 | grep -oE '[0-9]+')
[ -n "$count" ] && [ "$count" -ge 20 ] && [ "$count" -le 80 ] || { echo "detect produced '$count' candidates; expected 20-80"; exit 1; }

curl -fsS -X POST "$base/api/shutdown" >/dev/null
for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
if kill -0 "$pid" 2>/dev/null; then echo "sidecar did not exit after /api/shutdown"; exit 1; fi
wait "$pid" || true
echo "smoke ok: $APP"
```

If `bin/splitsmith` shebang handling from Task 2 was needed, the same fix applies here automatically since the script runs the bundled console script.

- [ ] **Step 2: Run it locally**

Run: `chmod +x desktop/smoke.sh && desktop/smoke.sh`
Expected: `smoke ok: ...`. Then run once with `SPLITSMITH_SMOKE_MODELS=/nonexistent desktop/smoke.sh` to confirm the download path works from the temp dir (takes a few minutes).

- [ ] **Step 3: Write `.github/workflows/desktop.yml`**

```yaml
name: desktop

on:
  pull_request:
    paths:
      - "desktop/**"
      - "pyproject.toml"
      - "uv.lock"
      - ".github/workflows/desktop.yml"
  push:
    tags: ["v*"]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  build:
    name: unsigned arm64 dmg + smoke
    runs-on: macos-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      - uses: actions/setup-node@v4
        with:
          node-version: 22
      - uses: pnpm/action-setup@v4
        with:
          version: 10.30.3
      - name: Cache models
        uses: actions/cache@v4
        with:
          path: ~/.splitsmith/models
          key: models-${{ hashFiles('src/splitsmith/models/registry.py') }}
      - name: Build (unsigned)
        env:
          CSC_IDENTITY_AUTO_DISCOVERY: "false"
        run: desktop/build.sh
      - name: Smoke
        run: desktop/smoke.sh
      - uses: actions/upload-artifact@v4
        with:
          name: Splitsmith-arm64-dmg
          path: desktop/dist/*.dmg
          retention-days: 14
```

Check the checkout/setup-uv versions `ci.yml` uses and match them. `macos-latest` is arm64 on GitHub-hosted runners; assert it in the build step with `[ "$(uname -m)" = arm64 ]` if in doubt.

- [ ] **Step 4: Push and watch the job**

Run: `git push -u origin desktop-packaging && gh pr create --fill --draft` then `gh run watch`.
Expected: green; the artifact lists one DMG. If the runner's model download exceeds the timeout, keep the cache step and raise `timeout-minutes` to 90 once.

- [ ] **Step 5: Commit**

```bash
git add desktop/smoke.sh .github/workflows/desktop.yml
git commit -m "ci(desktop): unsigned arm64 build with a sidecar smoke test"
```

### Task 11: Docs and the visual check

**Files:**
- Modify: `README.md` (under Install, after Option 1)
- Modify: `CLAUDE.md` (new section before "Multi-shooter comparison")

- [ ] **Step 1: README subsection**

```markdown
### Option 1b: macOS app (Apple Silicon)

A signed DMG of the same engine, no `uv` or Homebrew ffmpeg needed. Download
`Splitsmith-<version>-arm64.dmg` from the GitHub release, drag it to
Applications, open it. The app shares `~/.splitsmith` with the CLI install,
so a match you audited in one is there in the other. Splitsmith > Install
command line tool puts `splitsmith` on your PATH from the app's own runtime.
The first detection downloads the models (about 430 MB); the first overlay
render offers to download Chromium (about 260 MB). The app is built by
`desktop/build.sh`; see `docs/superpowers/specs/2026-09-20-electron-desktop-packaging-design.md`.
```

- [ ] **Step 2: CLAUDE.md section**

```markdown
## Desktop app (``desktop/``, spec 2026-09-20)

An Electron shell, not a second frontend: ``desktop/src/main.ts`` spawns
``Contents/Resources/python/bin/python3.12 -m splitsmith.ui.embedded``,
parses the ``SPLITSMITH_READY`` banner and loads the sidecar's URL. The
runtime is python-build-standalone plus the wheel (``build-runtime.sh``),
never PyInstaller; what ships is ``uv.lock``. The pure parts
(``sidecar.ts``, ``cliLink.ts``) have vitest tests; ``main.ts`` and
``menu.ts`` are wiring. ffmpeg is our own static build
(``build-ffmpeg.sh``, ``--lgpl`` for the #986 variant), published as a
GitHub release the app fetches by sha256; bumping it follows the ffmpeg
change rule above. The bundle is sealed: ``PYTHONDONTWRITEBYTECODE``,
precompiled bytecode, ``NUMBA_CACHE_DIR`` under ``~/Library/Caches``.
Data stays in ``~/.splitsmith``, shared with the CLI on purpose.
``CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh`` is the unsigned
build; ``desktop/smoke.sh`` runs the built sidecar and a detection;
``desktop/verify-signed.sh`` is what a signed build must pass. The one
engine surface added for it is ``ui/system_api.py`` (Chromium probe and
install, local only) and the button under ``PreviewPane``'s browser line.
```

- [ ] **Step 3: Visual check**

Launch the signed app from `/Applications` and take screenshots (`screencapture -w` or the Playwright MCP against the sidecar URL for the SPA pages) of: the loading page, the match picker, the About dialog with both versions, the notices window, and the failure page (`SPLITSMITH_RESOURCES=/nonexistent pnpm start` from `desktop/`). Show them to the user before calling the round done.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: desktop app install and the desktop/ section for Claude"
```

### Task 12: Whole-branch review pass

Per CLAUDE.md's review practice, before merging:

- [ ] Verify the specific claims: the runtime is relocatable after `uv pip install` (Task 2 step 3 moved it; re-run against the packaged `.app` copied to another path); `verify-signed.sh` was run on the DMG that will be released, not on an earlier build; `smoke.sh` passes against the signed app (signing can break a `.so` load if an entitlement is missing; the unsigned CI build cannot see that).
- [ ] Confirm each new test fails against the pre-change code (Task 8 step 6 did it for the router; do the same for the PreviewPane test by reverting the component change).
- [ ] Read the seams: `main.ts` failure page after READY (kill the sidecar with `kill -9` while the app runs and check the failure page appears); Cmd-Q leaves no `python3.12` behind; the CLI symlink survives an app update to a new `/Applications/Splitsmith.app` (same path, so yes; document that a renamed app breaks it).
- [ ] Run the full relevant suites: `uv run pytest tests/test_system_api.py tests/test_export_preview_api.py tests/test_ui_embedded.py -n0`, `cd src/splitsmith/ui_static && pnpm test && pnpm lint && pnpm typecheck`, `cd desktop && pnpm test && pnpm typecheck`.
- [ ] Open the PR for review (`gh pr ready`), with the DMG size, the notarization log id and the screenshots in the description.
