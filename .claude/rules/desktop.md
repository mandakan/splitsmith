---
paths:
  - "desktop/*.sh"
  - "desktop/*.yml"
  - "desktop/package.json"
  - "desktop/src/**"
  - "desktop/lib/**"
  - "desktop/linux/**"
  - "desktop/scripts/**"
  - "functions/**"
  - "scripts/ci/**"
  - ".github/workflows/desktop*.yml"
  - ".github/workflows/publish-pypi.yml"
  - ".github/workflows/release-please.yml"
  - "src/splitsmith/ui/embedded.py"
  - "src/splitsmith/ui/system_api.py"
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
  `create_app` path crashes exactly that install;
  `scripts/ci/assert_slim_import_surface.py` builds the local-mode app on a slim
  install to catch it.
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
GitHub release the app fetches by sha256. Bumping it follows the ffmpeg change
rule: **look at the frames, not the deltas**. Two compiles of the same x264
commit make different rate-control choices, so grid action frames can differ at
edges while being equivalent.

## Building and releasing

- `CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh` is the unsigned build;
  `desktop/smoke.sh` runs the built sidecar and a detection;
  `desktop/verify-signed.sh` is what a signed build must pass.
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
exists. `?platform=linux` waits for both the AppImage and the .deb; no parameter
stays the macOS answer. Moving the download behind a purchase changes that
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
  `/usr/bin/splitsmith` alone when it is not ours.
- The AppImage refuses with a dialog where the sandbox cannot start
  (`src/sandbox.ts`); `appImage.executableArgs: []` stops the stock desktop
  entry's unconditional `--no-sandbox`.
- `StartupWMClass` is `splitsmith-desktop` because the window class comes from
  package.json `name`. **Don't add `productName`**: it would move the macOS
  userData dir.
