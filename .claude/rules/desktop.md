---
paths:
  - "desktop/*.sh"
  - "desktop/*.yml"
  - "desktop/package.json"
  - "desktop/ffmpeg-pins.lock"
  - "desktop/entitlements.plist"
  - "desktop/src/**"
  - "desktop/lib/**"
  - "desktop/linux/**"
  - "desktop/scripts/**"
  - "functions/**"
  - "scripts/ci/**"
  - ".github/workflows/desktop*.yml"
  - ".github/workflows/windows-probe.yml"
  - ".github/workflows/publish-pypi.yml"
  - ".github/workflows/release-please.yml"
  - "src/splitsmith/ui/embedded.py"
  - "src/splitsmith/ui/system_api.py"
  - "src/splitsmith/runtime.py"
  - "src/splitsmith/raw_link.py"
  - "src/splitsmith/youtube/oauth.py"
  - "scripts/bake_youtube_client.py"
  - "pyproject.toml"
---

# The desktop app (`desktop/`, spec 2026-09-20)

An Electron shell, not a second frontend: `desktop/src/main.ts` spawns
`Contents/Resources/python/bin/python3.12 -m splitsmith.ui.embedded`, parses the
`SPLITSMITH_READY` banner and loads the sidecar's URL. The pure parts
(`sidecar.ts`, `cliLink.ts`, `updateCheck.ts`, `sandbox.ts`) have vitest tests;
`main.ts` and `menu.ts` are wiring.

## The runtime

- python-build-standalone plus the wheel (`build-runtime.sh`), **never
  PyInstaller**. What ships is `uv.lock`'s default set.
- **The slim install**: a module-level `splitsmith.db` import anywhere on the
  `create_app` path crashes exactly that install (a local-only API module such as
  `ui/youtube_api.py` importing it at top level is the usual way);
  `scripts/ci/assert_slim_import_surface.py` builds the local-mode app on a slim
  install to catch it. A new default dependency in `pyproject.toml` ships in
  every bundle.
- Every console script in the runtime's `bin/` gets the relocatable sh
  shim (`build-runtime.sh`), whichever header uv wrote: a long build path
  makes uv emit an `#!/bin/sh` polyglot with the absolute path on line 2
  instead of a shebang. `release.sh`'s `$TMPDIR` worktree is long enough and
  CI's checkout is not, so only shipped DMGs broke (0.87.2's CLI pointed at
  the deleted worktree), and `smoke.sh` passed because the worktree still
  existed. The build fails if any `bin/` file still names the build dir.
  Windows has no shebang to rewrite: uv's `Scripts\*.exe` launchers hold the
  interpreter's absolute path, so `scripts/windows_console_shims.py` replaces
  ours with `<name>.cmd` + `<name>-script.py` (finding `..\python.exe` from
  `%~dp0`) and deletes everything else in `Scripts\` (numba also drops a
  plain script there with a shebang); the same stale-path check runs, in
  both the POSIX and the `C:\` spelling.
- The bundle is sealed: `PYTHONDONTWRITEBYTECODE`, precompiled bytecode,
  `NUMBA_CACHE_DIR` under `~/Library/Caches`. Data stays in `~/.splitsmith`,
  shared with the CLI on purpose.
- `POST /api/shutdown` ends the sidecar (`exit_event` in `embedded.py`), which
  is what makes Cmd-Q take half a second instead of the SIGTERM grace period.
- The one engine surface added for the app is `ui/system_api.py` (Chromium probe
  and install, local only) and the button under `PreviewPane`'s browser line.

## ffmpeg

Our own static build (`build-ffmpeg.sh`; `--lgpl` is the LGPL variant; the
pipeline only ever names `fontfile=`, so there is no fontconfig), published as a
GitHub release the app fetches by sha256 (pins in `desktop/fetch-ffmpeg.sh` and
`desktop/ffmpeg-pins.lock`). To bump it, render `scripts/render_match_frames.py`
and `scripts/render_grid_frames.py` with the old and the new build and **look at
the frames, not the deltas**: single-shooter frames must be pixel-identical;
grid action frames may differ at edges only (two compiles of the same x264
commit make different rate-control choices); text placement, colours and timing
must not move.

## Building and releasing

- `CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh` is the unsigned build;
  `desktop/smoke.sh` runs the built sidecar and a detection;
  `desktop/launch-check.sh` starts Electron itself and, on a Mac, checks that
  quitting it takes the sidecar along; `desktop/verify-signed.sh` is what a
  signed build must pass.
- **CI checks the DMG, never signs it.** `desktop/check-dmg.sh` opens a DMG the
  way a user does (layout, arm64, the version, a `ditto` install) and the smoke
  and launch checks run that installed copy, not `dist/mac-arm64`, which is
  only what electron-builder packed. `desktop-macos.yml` runs it unsigned on
  PRs and, chained after `publish-pypi`, from the release's PyPI wheel (red
  there means don't start `release.sh`), then again ad-hoc signed under the
  hardened runtime, which catches a dropped `allow-jit` (Electron crashes) but
  not a dropped `allow-unsigned-executable-memory` (numba still JITs). `desktop-macos-verify.yml` downloads
  the signed DMG `release.sh` uploaded, quarantines it like a browser does and
  adds Gatekeeper, the ticket and `syspolicy_check`; `release.sh` dispatches it.
- `desktop/release.sh [vX.Y.Z]` builds the tag (default the newest release)
  signed in a throwaway worktree, smokes it and uploads the DMG to the GitHub
  release, reading the `APPLE_API_*` notarization vars from
  `~/.appstoreconnect/splitsmith-desktop.env`. **Signing stays local on
  purpose.**
- **Every shipped desktop build, DMG and Linux alike, bundles the wheel PyPI
  serves, never one built from a checkout**: only `publish-pypi.yml` bakes the
  YouTube OAuth client in (a DMG built from a checkout once shipped with empty
  constants). `release.sh` fetches it with `scripts/ci/fetch_pypi_wheel.py` and
  passes `build.sh --wheel`; release builds set
  `SPLITSMITH_REQUIRE_YOUTUBE_CLIENT=1`, which makes `build-runtime.sh` run
  `desktop/scripts/check_youtube_client.py` and refuse a bundle without the
  client.
- `desktop/scripts/cdp-shot.mjs` screenshots the window when Electron runs with
  `--remote-debugging-port`.

## Updates

`desktop/src/updateCheck.ts` (pure, tested) plus `updates.ts` (fetch, the
dismissed file under userData, the sheet). The feed is the Pages Function
`functions/desktop/latest.json.js` on splitsmith.app: it picks the newest
`v*.*.*` release that has a `.dmg` attached, because GitHub's `releases/latest`
can be an ffmpeg source release and release-please publishes before the DMG
exists. `?platform=linux` waits for both the AppImage and the .deb,
`?platform=win32` for an `.exe`; no parameter stays the macOS answer. Moving the download behind a purchase changes that
function only. **No auto-update.**

## Linux

- x86_64 AppImage and .deb, built by `.github/workflows/desktop-linux.yml` in
  `ubuntu:22.04` (the glibc 2.35 floor) from the PyPI wheel, chained after
  `publish-pypi` in `release-please.yml`. PRs build and smoke it through
  `desktop.yml` without uploading; a failed release is re-run with
  `workflow_dispatch` and the tag.
- `desktop/build.sh --linux` builds locally; `desktop/lib/target.sh` is the one
  place a target is resolved (`host_target`).
- The Linux ffmpeg is our own static build (`build-ffmpeg-linux.sh`); its tag
  lands in `desktop/build/FFMPEG_RELEASE`, **never in `build/bin`**, which ships
  verbatim into the bundle.
- The deb's `linux/postinst.sh` / `postrm.sh` are electron-builder's stock
  scripts plus `linux/cli-link.sh`, and must stay that way
  (`desktop/scripts/check-deb.sh` in CI); inside them `${letters}` is an
  electron-builder macro. `cli-link.sh` installs `/usr/bin/splitsmith` as a
  `#!/bin/sh` wrapper, not a symlink, so terminal runs get the bundled ffmpeg
  (only the Electron sidecar sets `SPLITSMITH_FFMPEG`, and the CLI looks next to
  its interpreter, not in `resources/bin`). It leaves an existing
  `/usr/bin/splitsmith` alone, on install and removal, when it is not ours.
- The AppImage refuses with a dialog where the sandbox cannot start
  (`src/sandbox.ts`); `appImage.executableArgs: []` stops the stock desktop
  entry's unconditional `--no-sandbox`.
- `StartupWMClass` is `splitsmith-desktop` because the window class comes from
  package.json `name`. **Don't add `productName` to package.json** (it would move
  the macOS userData dir); `electron-builder.yml`'s own `productName` is fine.

## Windows (not shipped)

There is no Windows build yet (#1765). The shell already knows win32:
`sidecar.ts` runs `python\python.exe` with `bin\ffmpeg.exe`, logs and caches
numba under `%LOCALAPPDATA%\splitsmith` (the engine's cache dir), the menu
takes the Linux shape (no CLI installer), `main.ts` sets the AppUserModelID
to `electron-builder.yml`'s `appId`, and the feed answers `?platform=win32`.
`build-runtime.sh --target windows-x86_64` builds the runtime under Git Bash
(`lib/target.sh` maps MINGW/MSYS to it), and `desktop-windows.yml` (PRs,
through `desktop.yml`) runs the slim import check and `smoke.sh` against the
bare runtime dir with Chocolatey's ffmpeg as a stand-in. `smoke.sh` hands the
engine `cygpath -w` paths, as the shell would. Our own ffmpeg, the installer,
signing and the release job are still to do.
`windows-probe.yml` runs the suite on
`windows-latest` (Python 3.12, as the bundle ships) on every push to main and
reports what breaks in the run summary; it is not a gate, and test failures
never turn it red. `scripts/ci/windows_probe.py` also imports every module on
its own, because one POSIX-only import on the conftest path stops collection
and hides everything behind it. `PYTHONUTF8` stays unset there on purpose: the
bundle would run without it.

"Reference in place" is a symlink on POSIX and a pointer file,
`raw/<name>.ref` holding the source's path, on Windows (a symlink there
needs Developer Mode); `raw/<name>` stays the stored, synced identity on
both. Every reader or writer of a `raw/` entry goes through
`splitsmith.raw_link`, and a confined (hosted) project never follows a
pointer (`tests/test_raw_pointer_links.py` runs the pointer path on any host).
