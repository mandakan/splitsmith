# Electron desktop packaging (macOS, direct download)

Date: 2026-09-20. Status: approved design, not yet implemented.

## Goal

A signed, notarized `Splitsmith-<version>-arm64.dmg` that a person can
download, open and run with no terminal, no `uv`, no Homebrew ffmpeg.
The app is a thin Electron shell around the existing engine: it launches
`splitsmith.ui.embedded` from a Python runtime inside the bundle, waits
for the `SPLITSMITH_READY` handshake and loads the sidecar's URL in a
window. Nothing in the SPA or the server learns it is inside Electron,
apart from one new route for the on-demand Chromium install.

Decisions taken during brainstorming, in order:

1. Target is a direct-download DMG, not the Mac App Store. No sandbox,
   so bundled GPL ffmpeg is fine with source compliance and Playwright's
   runtime Chromium download stays. #986 (dependency cleanup for a store
   build) becomes a parallel track, not a gate.
2. The shell lives in this repo under `desktop/`. Lifting it into a
   private repo later is a move, not a rewrite; #371's sidecar publish
   is useful either way.
3. macOS Apple Silicon only this round.
4. Lean DMG: models and Chromium are fetched on first use into the
   shared caches, not bundled.
5. Signing and notarization run locally from the build script with the
   identity in the login keychain. CI builds unsigned and runs the smoke
   test; promoting signing to CI is a follow-up.
6. The Python runtime ships as a relocatable python-build-standalone
   interpreter with the wheel installed into it (not PyInstaller).
7. The app shares `~/.splitsmith` with the CLI and can install the CLI
   itself.
8. ffmpeg is built from source by our own script so the LGPL variant is
   one flag away.

## Layout

```
desktop/
  package.json           electron, electron-builder, @electron/notarize as devDependencies
  electron-builder.yml   mac dmg, arm64, extraResources, entitlements, notarize
  entitlements.plist
  src/main.ts            Electron main process
  src/preload.ts         exposes app version + engine version to loading.html only
  src/loading.html       shown until READY; doubles as the startup-failure page
  build-runtime.sh       assembles build/runtime/python (interpreter + wheel)
  build-ffmpeg.sh        builds static ffmpeg/ffprobe from source (--lgpl flag)
  fetch-ffmpeg.sh        downloads the published ffmpeg asset by sha256 into build/bin
  build-notices.sh       generates NOTICES.md from the two dependency trees
  smoke.sh               exercises the built app's sidecar
  NOTICES.head.md        hand-written part of the notices (ffmpeg, Electron, fonts, models)
```

`desktop/` has its own `package.json`, like `ui_static/`. It is not a
workspace member of the root `package.json` (that one is the marketing
site's wrangler shim).

## Build pipeline

`pnpm --dir desktop build` runs, in order:

1. `pnpm --dir src/splitsmith/ui_static build` and delete the source
   maps, exactly as `publish-pypi.yml` does.
2. `uv build --wheel` at the repo root. The wheel carries the SPA dist
   (`[tool.hatch.build] artifacts`).
3. `build-runtime.sh`:
   `uv python install --install-dir desktop/build/runtime 3.12` fetches a
   python-build-standalone `install_only` build, which is relocatable.
   `uv pip install --python <that interpreter> --break-system-packages
   dist/splitsmith-<ver>-py3-none-any.whl` installs the default
   dependency set into the interpreter's own site-packages. No extras:
   `psycopg` and the hosted stack never enter the bundle. Then
   `python -m compileall -q` over site-packages so no `.pyc` is written
   after signing.
4. `fetch-ffmpeg.sh` downloads `ffmpeg` and `ffprobe` from the pinned
   GitHub release asset (see ffmpeg below) into `desktop/build/bin/`,
   verifying sha256.
5. `build-notices.sh` writes `desktop/build/NOTICES.md`.
6. `electron-builder --mac --arm64`. `extraResources` copies
   `build/runtime/python` to `Contents/Resources/python`, `build/bin` to
   `Contents/Resources/bin`, and `NOTICES.md` to `Contents/Resources/`.
   Output: `desktop/dist/Splitsmith-<ver>-arm64.dmg`.

The version is read from `pyproject.toml` at build time and written into
`desktop/package.json`'s `version` before electron-builder runs, so
release-please's bump is the only bump. `desktop/package.json` is
committed with `"version": "0.0.0"` and the build script rewrites it in
the working tree only.

Fast iteration: `CSC_IDENTITY_AUTO_DISCOVERY=false pnpm --dir desktop
build` skips signing and notarization; `pnpm --dir desktop start` runs
Electron against `build/runtime` without packaging.

## Runtime wiring (Electron main)

On `app.whenReady`:

- Take `app.requestSingleInstanceLock()`; a second launch focuses the
  existing window and exits.
- Create the window on `loading.html` (brand mark, "Starting engine",
  the app version).
- Pick a free port by binding and releasing a TCP socket on 127.0.0.1.
- Spawn `Contents/Resources/python/bin/python3.12 -m splitsmith.ui.embedded`
  with a clean environment carrying:
  - `SPLITSMITH_PORT=<port>`, `SPLITSMITH_HOST=127.0.0.1`
  - `SPLITSMITH_FFMPEG` and `SPLITSMITH_FFPROBE` pointing at
    `Contents/Resources/bin/`. Explicit rather than relying on the
    executable-adjacent search in `runtime.py`, because the interpreter
    is not the sidecar binary.
  - `NUMBA_CACHE_DIR=~/Library/Caches/Splitsmith/numba` (the bundle is
    read-only and sealed by the signature; numba's default cache is next
    to the module).
  - `PYTHONDONTWRITEBYTECODE=1`, `PYTHONNOUSERSITE=1`, and no `PYTHONPATH`
    or `PYTHONHOME` inherited from the user's shell.
  - `--log-dir ~/Library/Logs/Splitsmith` on the argv (`embedded.py`
    already takes it).
  - `SPLITSMITH_PROJECT_ROOT` unset: the server opens on the match
    picker, the same as `splitsmith ui` without `--project`.
- Read the sidecar's stderr line by line. On the `SPLITSMITH_READY` line
  parse the JSON, `loadURL(base_url)`, and keep the handle for shutdown.
  Stderr keeps flowing to the log file after that.
- If the process exits before READY, or READY does not arrive within
  30 s, `loading.html` switches to its failure state: the last 50 stderr
  lines in a monospace block, an "Open log folder" button, and "Quit".
  That is the whole error UI for this round.
- After READY, if the sidecar exits, show the same failure page in the
  window (replacing the SPA) so the user is never left with a dead page.
- `setWindowOpenHandler` and `will-navigate`: any URL not on the
  sidecar's origin goes to `shell.openExternal`. Share links, YouTube
  consent and the marketing site open in the default browser.
- On `before-quit`: `POST /api/shutdown`, wait up to 10 s for exit, then
  SIGTERM, then SIGKILL after 5 more seconds.

Web preferences: `contextIsolation: true`, `nodeIntegration: false`,
`sandbox: true`. The preload exposes only `{ appVersion, engineVersion }`
for the loading and about pages. The SPA gets no bridge; it does not
need one.

Menu (application menu, standard macOS shape):

- Splitsmith > About: app version, engine version (from the READY
  payload), "Third-party notices" (opens `NOTICES.md` in a window).
- Splitsmith > Install command line tool: symlinks
  `Contents/Resources/python/bin/splitsmith` to
  `/usr/local/bin/splitsmith`, prompting for admin rights through
  `osascript` when the directory is not writable, the way VS Code
  installs `code`. Shows "Installed" / "Already installed" / the error.
- File > Open log folder.
- Standard Edit, View (reload, zoom, dev tools behind Option), Window.

## Data location

The app uses `~/.splitsmith` exactly as the CLI does: `config.yaml`,
`projects.json`, the job journal, `youtube.json`, `models/`. Reasons:

- The job journal is already multi-instance safe (per-instance lock
  tokens in `jobs.sqlite3.locks/`, sqlite WAL), so the app and a
  `splitsmith ui` from a checkout can run at once.
- The model cache is content-addressed by sha256, so two engine
  versions with different manifests share it without re-downloading.
- One YouTube login, one recent-projects list, one config.

`projects.json` is read-modify-write with an atomic replace and no
cross-process lock. Two processes writing in the same instant can lose
one recent-projects entry. Accepted; not worth a lock.

Version skew between the app's engine and a separately installed wheel
is the real concern, not concurrency. The DMG and the wheel are cut
from the same tag, so the numbers match unless the user upgrades one
and not the other. The existing sync rule applies: a newer engine writes
docs an older one can still read. The About panel shows the engine
version so it is visible which one touched a project. The "Install
command line tool" menu item exists so that a DMG user never needs a
second engine at all.

## ffmpeg

`desktop/build-ffmpeg.sh` builds a static `ffmpeg` and `ffprobe` from a
pinned FFmpeg release with pinned x264, freetype, harfbuzz and
fontconfig, configured with `--enable-gpl --enable-libx264
--enable-videotoolbox --enable-audiotoolbox --enable-libfreetype
--enable-libharfbuzz --enable-libfontconfig` and nothing else external
(#986's inventory of what the pipeline uses). With `--lgpl` it drops
`--enable-gpl` and libx264 and emits the LGPL variant. That flag is not
used by the desktop build this round; the pipeline still has five
hardcoded `libx264` call sites, and switching is #986's encoder work.

The build runs once per bump, locally or through a manually dispatched
workflow, and the output tarball is attached to a
`ffmpeg-macos-arm64-<ffmpeg-version>-<recipe-rev>` GitHub release in this
repo together with the script and the pinned source tarballs. That
release is the complete corresponding source offer GPL-3 requires, and
its URL is ours, so it cannot be pruned under us the way BtbN's
autobuilds are (#962). `fetch-ffmpeg.sh` pins the asset URL and sha256.

The recipe follows the repo's ffmpeg-change rule: bumping the pin
re-renders the fixture frames and diffs them.

## Notices

`build-notices.sh` concatenates `NOTICES.head.md` (hand-written:
FFmpeg GPL-3 text and the link to the source release, Electron MIT,
Antonio / JetBrains Mono / Geist under OFL-1.1, the CLAP and PANNs
weights with the license found on their model cards, recorded here as
#986's "verify model weight licenses" task asks) with the output of
`pip-licenses` over the bundled site-packages and `license-checker`
over `ui_static`'s production dependencies. The result ships as
`Contents/Resources/NOTICES.md`, is reachable from the About panel, and
is copied to the DMG root.

## Signing and notarization

Prerequisite, once: a Developer ID Application certificate in the login
keychain (Xcode > Settings > Accounts > Manage Certificates, or the
developer portal). `security find-identity -v -p codesigning` lists none
today.

electron-builder signs every Mach-O it finds under the app bundle with
hardened runtime: Electron, the interpreter, every extension `.so`,
Playwright's bundled `node` driver, ffmpeg and ffprobe.
`entitlements.plist` carries exactly:

- `com.apple.security.cs.allow-jit` (Electron)
- `com.apple.security.cs.allow-unsigned-executable-memory` (llvmlite's
  JIT under numba, used by librosa)

Notarization is electron-builder's `notarize: true`, credentials from
`APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER` in the
environment (the same App Store Connect API key the iOS publish flow
uses). The build script ends with `codesign --verify --deep --strict`,
`spctl --assess --type execute` and `xcrun stapler validate` on the app,
and fails on any of them.

The bundle is never written to after signing: bytecode is precompiled,
`PYTHONDONTWRITEBYTECODE=1` is set, numba's cache is redirected, and
Playwright installs its browser under `~/Library/Caches/ms-playwright`
(its default).

## First-run downloads

Models: no change. The server already fetches missing artifacts on the
first detection with progress reporting, into the shared cache.

Chromium: today the only path is the manual `uv run playwright install
chromium --only-shell` hint in `overlay_raster.INSTALL_HINT`, which a
desktop user cannot act on. New in the engine:

- `POST /api/system/chromium/install` (local mode only; hosted answers
  404 like the YouTube routes). Enqueues a job that runs
  `[sys.executable, "-m", "playwright", "install", "chromium",
  "--only-shell"]` and reports its output as progress. The job is
  idempotent; a second call while one runs returns the running job.
- `GET /api/system/chromium` reports `{installed: bool, path: str | None}`
  using the same probe `ChromiumRasterizer` uses.
- The Export page maps the rasterizer's browser-missing error to an
  "Install renderer (260 MB)" button that calls the route and follows the
  job through the existing progress strip. The existing error text stays
  for the CLI.

This is the one engine feature in the round. Everything else is wiring.

## Verification

`desktop/smoke.sh` runs against the built `.app`, not the checkout:

1. Launches `Contents/Resources/python/bin/python3.12 -m
   splitsmith.ui.embedded` with `SPLITSMITH_CONFIG_DIR` at a temp dir and
   the ffmpeg env vars pointing into the bundle, parses READY.
2. Asserts `GET /api/health` is 200 and reports the expected version.
3. Asserts the server's ffmpeg capabilities report the bundled binary
   and list `h264_videotoolbox`.
4. Runs a beep and shot detection on a `tests/fixtures` clip through the
   API and compares against the fixture's ground truth within the usual
   tolerance. Models come from the cache the temp config dir points at;
   in CI that means a download, cached across runs.
5. Shuts down through `/api/shutdown` and asserts a clean exit.

CI: a `desktop` job in `ci.yml` on `macos-latest` (arm64), triggered on
pull requests that touch `desktop/`, `pyproject.toml` or `uv.lock`, and
on release tags. It builds unsigned, runs `smoke.sh`, and uploads the
DMG as a workflow artifact. Signing stays local this round.

Visual check before calling it done: launch the signed app and show
the loading page, the match picker, the About panel with both versions,
and the failure page (provoked by pointing `SPLITSMITH_FFMPEG` at a
missing file), as screenshots.

## Out of scope

Auto-update, Intel and Windows builds, Mac App Store and the remainder
of #986 (LGPL switch, encoder owner, soxr, Playwright replacement),
in-app billing, and any change to the SPA beyond the Chromium install
button.
