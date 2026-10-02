# Desktop app on Linux (AppImage + .deb)

Date: 2026-10-02. Status: approved design, not yet implemented.

## Goal

A public Linux download of the Electron desktop app, attached to every
`vX.Y.Z` GitHub release next to the DMG, that runs on Arch and Debian
with no terminal, no `uv` and no system ffmpeg. Same shape as the macOS
app (spec 2026-09-20): a thin Electron shell around
`splitsmith.ui.embedded`, python-build-standalone plus the wheel, our
own static ffmpeg, a bundle that is never written to at runtime.

Hard constraint: nothing changes for the macOS app, the hosted
deployment or a `uvx splitsmith` install. Every edit below is either
Linux-only or keeps the macOS path byte-identical, and that is pinned
by tests where a test can pin it.

Decisions taken during brainstorming, in order:

1. **Audience: other shooters, published.** Not a personal build; distro
   coverage, the update feed and the sandbox handling are in scope.
2. **Built and released in GitHub Actions.** Linux needs no signing, so
   unlike `desktop/release.sh` nothing has to stay local.
3. **CLI: the .deb installs `/usr/bin/splitsmith`; the AppImage offers
   none.** The menu item "Install command line tool" is macOS-only.
   AppImage users who want the CLI use `uv tool install splitsmith`.
4. **Sandbox: .deb is the Ubuntu format; the AppImage refuses with a
   dialog where the sandbox cannot start.** Never a silent
   `--no-sandbox` fallback.

## Scope

In: `x86_64` AppImage and `.deb`, `SHA256SUMS`, the release job, a PR
build job, the update feed's platform split, the Linux-only app code.

Out: arm64 Linux, `.rpm`, Flatpak, Snap, an apt repository, auto-update,
a download section on splitsmith.app (the GitHub release page is the
download, as it is for the DMG today), any Python change.

## Platform facts the design rests on

- **Arch**: the kernel allows unprivileged user namespaces, so
  Electron's namespace sandbox works inside an AppImage.
- **Debian 11+**: unprivileged user namespaces are on by default. Both
  formats work.
- **Ubuntu 24.04+**: AppArmor restricts unprivileged user namespaces. An
  AppImage cannot ship the setuid `chrome-sandbox`, so Electron aborts
  at startup, before any JS runs. A `.deb` sets the helper setuid at
  install (and electron-builder's deb ships an AppArmor profile; verify
  the version in `pnpm-lock.yaml` does).
- **The engine already handles Linux.** `runtime._platform_cache_dir`,
  `user_config.user_config_dir`, `system_check` and the reveal-in-folder
  route in `ui/server.py` all have Linux branches, because uvx on Linux
  is supported today. The sidecar never sets `SPLITSMITH_HOME`, so on
  Linux the app's data lands in `~/.config/splitsmith` (or
  `$XDG_CONFIG_HOME/splitsmith`), shared with the uvx CLI, the same way
  the macOS app shares `~/.splitsmith`.

## Artifacts

Per release `vX.Y.Z`:

- `Splitsmith-X.Y.Z-x86_64.AppImage`
- `splitsmith-desktop_X.Y.Z_amd64.deb`
- `SHA256SUMS` over the two

Built in an `ubuntu:22.04` container. Its glibc (2.35) is the floor:
Debian 12+, Ubuntu 22.04+, Arch. Electron 44, onnxruntime's manylinux
wheels and python-build-standalone (glibc 2.17) all sit under it.

## Runtime and ffmpeg

**`build-runtime.sh`** takes a target (`macos-aarch64`, the default, or
`linux-x86_64`) and a `--wheel <path>` option (default: newest
`dist/splitsmith-*.whl`, today's behaviour). On Linux it installs the
baseline `x86_64-unknown-linux-gnu` build explicitly, never an
`x86_64_v2/v3/v4` variant: a v3 build dies with an illegal instruction
on older CPUs, and uv's default choice on a v3 host is not something to
rely on. Everything after the install is shared and unchanged: the
psycopg and SPA sentinels, the relocatable console-script shim
(`readlink -f` exists on every Linux), `compileall`, the pruning.

**ffmpeg.** `build-ffmpeg.sh` gains a Linux branch: static, built in the
same `ubuntu:22.04` container from the same pinned sources, freetype for
`drawtext` (the pipeline only ever names `fontfile=`, so no
fontconfig), GPL variant. Published as our own GitHub release
`ffmpeg-linux-x86_64-<ver>-r1` with sources attached. `fetch-ffmpeg.sh`
reads tag, asset and sha256 per platform from `ffmpeg-pins.lock` and
verifies with `sha256sum` on Linux, `shasum -a 256` on macOS. Bumping it
follows the ffmpeg change rule: render frames with
`scripts/render_match_frames.py` and `scripts/render_grid_frames.py`
through the Linux build and compare them with the macOS build's before
the first release.

## electron-builder

`electron-builder.yml` gains a `linux:` block; `mac:` and `dmg:` do not
change.

```yaml
linux:
  target:
    - target: AppImage
      arch: [x64]
    - target: deb
      arch: [x64]
  category: AudioVideo
  executableName: splitsmith-desktop
  icon: build-resources/icons   # PNG set, 512x512 minimum
appImage:
  artifactName: ${productName}-${version}-x86_64.${ext}
deb:
  artifactName: splitsmith-desktop_${version}_amd64.${ext}
  depends: [libnss3, libgbm1, libasound2, libgtk-3-0, libxss1, libnotify4, xdg-utils]
  afterInstall: linux/postinst.sh
  afterRemove: linux/postrm.sh
afterPack: scripts/afterPack.cjs
```

The deb installs into `/opt/Splitsmith`. `postinst.sh` links
`/usr/bin/splitsmith` to `/opt/Splitsmith/resources/python/bin/splitsmith`
(the relocatable shim resolves the link back into the bundle);
`postrm.sh` removes the link only when it still points into
`/opt/Splitsmith`. The exact depends list is settled against a clean
Debian 12 container during implementation; the list above is the
starting point.

`build.sh` takes `--mac` or `--linux` (default: the host OS) and the
optional `--wheel`, and passes the target to `build-runtime.sh`,
`fetch-ffmpeg.sh` and electron-builder. A `--mac` run executes exactly
today's steps; the signing check stays macOS-only.

## App code (`desktop/src` only)

**`sidecar.ts`.** `SidecarOptions` gains `platform: NodeJS.Platform`,
passed by `main.ts` from `process.platform`, so the function stays pure.

- `darwin`: today's spec, byte for byte (pinned by the existing tests,
  which gain an explicit `platform: "darwin"`).
- `linux`: `logDir` is `$XDG_STATE_HOME/splitsmith/logs`, falling back
  to `~/.local/state/splitsmith/logs`; `NUMBA_CACHE_DIR` is
  `$XDG_CACHE_HOME/splitsmith/numba`, falling back to
  `~/.cache/splitsmith/numba`, beside the engine's own Linux cache dir.
  An XDG value that is not absolute is ignored, as `user_config` does.
  The `PYTHON*` / `SPLITSMITH_*` scrub is unchanged.

**`menu.ts`.** A pure `menuTemplate(platform, handlers)` returns the
template; `buildMenu` applies it.

- `darwin`: today's menu.
- `linux`: no app-name menu (`hide`, `hideOthers`, `unhide` are macOS
  roles). File: "Open log folder", separator, Quit. Edit and View as
  today. Help: "About Splitsmith", "Third-party notices", "Check for
  updates...". No "Install command line tool".

A vitest file pins both templates' labels and roles.

**`main.ts`.** On Linux the `BrowserWindow` gets `icon` from the bundled
512 px PNG (GNOME and KDE show none otherwise). And before anything else
it checks `SPLITSMITH_SANDBOX_BLOCKED` (below).

**`updateCheck.ts`.** `feedUrl(platform)`: `darwin` returns
`UPDATE_FEED_URL` unchanged; `linux` appends `?platform=linux`.
`updates.ts` calls it.

**Unchanged:** `cliLink.ts` (macOS-only, unreachable on Linux because
the menu does not offer it), the SPA, every Python module.

## The AppImage sandbox check

Electron aborts before JS when its sandbox cannot start, so the check
has to run before Electron. `scripts/afterPack.cjs` (electron-builder
hook, Linux only) renames the packaged `splitsmith-desktop` binary to
`splitsmith-desktop.bin` and writes `linux/launcher.sh` in its place:

```sh
#!/bin/sh
here="$(dirname "$(readlink -f "$0")")"
bin="$here/splitsmith-desktop.bin"
if [ -z "${APPIMAGE:-}" ] || unshare -Ur true 2>/dev/null; then
  exec "$bin" "$@"
fi
SPLITSMITH_SANDBOX_BLOCKED=1 exec "$bin" --no-sandbox "$@"
```

- Not an AppImage (`$APPIMAGE` unset, the .deb case): exec unchanged.
  The deb's setuid helper and AppArmor profile handle the sandbox.
- AppImage and `unshare -Ur true` succeeds (Arch, Debian): exec
  unchanged; the namespace sandbox works.
- AppImage and it fails (Ubuntu 24.04+): exec with `--no-sandbox` and
  `SPLITSMITH_SANDBOX_BLOCKED=1`. `main.ts` checks that variable first:
  it shows one native `dialog.showMessageBox` ("This system blocks the
  sandbox an AppImage needs. Install the .deb from the release page
  instead.") with a button opening the GitHub release page in the
  browser, then `app.quit()`. It never creates a `BrowserWindow`, never
  starts the sidecar and never loads web content, so the unsandboxed
  process only draws a dialog.

The launcher's branch logic is tested in vitest by running it as a
subprocess against a stub `.bin` that echoes its argv and env, with a
stub `unshare` on `PATH` that succeeds or fails, and `APPIMAGE` set or
unset: four cases.

## Update feed

`functions/desktop/latest.json.js`: `pickLatest(releases, platform)`
and `onRequestGet({ request })` reading `?platform=`.

- No parameter, which is every shipped macOS app: unchanged. A release
  counts once a `.dmg` is attached.
- `platform=linux`: a release counts once both an `.AppImage` and a
  `.deb` are attached.
- Any other value: 400, so a typo is not served the macOS answer.

Response shape unchanged. The edge cache already keys on the full URL,
query included. Tests: the existing no-parameter cases stay as they
are; new cases pin that a DMG-only release is not announced to Linux, a
Linux-only release is not announced to macOS, and a release with only
one of the two Linux assets is not announced to Linux.

## Release pipeline

**The wheel comes from PyPI.** `publish-pypi.yml` bakes the YouTube
OAuth client into the wheel (`scripts/bake_youtube_client.py`); a wheel
built from a checkout has empty constants. The Linux job therefore never
builds its own wheel: it reads `https://pypi.org/pypi/splitsmith/X.Y.Z/json`,
downloads the `py3-none-any` wheel it lists, checks the listed sha256
and passes the file to `build.sh --wheel`. The bundle then ships the same wheel uvx users get.

**`release-please.yml`** gains `desktop-linux`, chained like
`publish-image`:

```yaml
desktop-linux:
  needs: [release-please, publish-pypi]
  if: needs.release-please.outputs.release_created == 'true'
  uses: ./.github/workflows/desktop-linux.yml
  with:
    release_tag: ${{ needs.release-please.outputs.tag_name }}
  permissions:
    contents: write
```

`desktop-linux.yml` (reusable, also `workflow_dispatch` with a tag
input for re-runs): `ubuntu-latest` runner, `ubuntu:22.04` container,
build, smoke, launch check, `sha256sum` into `SHA256SUMS`,
`gh release upload <tag> ... --clobber`. A failure leaves the release
without Linux assets; the feed's both-assets rule keeps Linux apps from
being told about that version, and a `workflow_dispatch` re-run
finishes it. PyPI's CDN can lag the publish by a minute; the download
step retries for up to five minutes.

**`desktop.yml`** gains a `linux` job on the same triggers as the macOS
one: same container, a checkout-built wheel (no YouTube client needed
for a smoke), build, smoke, launch check, artifacts uploaded with
14-day retention. The macOS job does not change.

## Smoke and launch check

`smoke.sh` takes the resources directory instead of an `.app` path; a
small wrapper maps `dist/mac-arm64/Splitsmith.app` to
`.../Contents/Resources` and `dist/linux-unpacked` to
`.../resources`, so the macOS invocation keeps working. The encoder
assertion is per platform: `h264_videotoolbox` on macOS; `libx264`
and the `drawtext` filter on Linux. The detection through the bundled
CLI is shared.

Launch check (Linux job only): `--appimage-extract` the built AppImage
(CI has no FUSE), run `squashfs-root/splitsmith-desktop.bin
--no-sandbox` under `xvfb-run`, parse the sidecar's `SPLITSMITH_READY`
banner from the app log, wait for `/api/health`, then `POST
/api/shutdown`. That proves Electron, the packed `main.js` and the
sidecar chain start from the AppImage's own contents, which `smoke.sh`
alone (sidecar only) does not. It bypasses the launcher on purpose:
Docker's default seccomp profile blocks `unshare`, and the
`ubuntu-latest` host is 24.04 with restricted namespaces, so the
launcher would correctly take the dialog branch in both places. The
launcher's branches are covered by its vitest cases and the manual
checklist.

## Manual verification before the first public Linux release

One pass per row, results recorded on the PR:

- AppImage on Arch: opens, detects a seeded clip (`seed_demo_match.py
  --media`), renders an export.
- AppImage on Debian 12: same.
- .deb on Debian 12: same, plus `splitsmith --version` from a terminal
  and a detection through `/usr/bin/splitsmith` that the app then sees
  (shared `~/.config/splitsmith`).
- .deb on Ubuntu 24.04: opens with the sandbox on (no `--no-sandbox` in
  `ps`).
- AppImage on Ubuntu 24.04: the dialog appears, the button opens the
  release page, the process exits.
- A uvx install on the same Debian box before and after installing the
  .deb: same data, same matches.
- Removing the .deb removes `/usr/bin/splitsmith` and leaves
  `~/.config/splitsmith` alone.
- Frames from the Linux ffmpeg compared with the macOS build's.

Use a scratch `SPLITSMITH_HOME` or auto-sync off for every check; never
a real hosted token.

## Open item found during design (not part of this work)

`desktop/build.sh` builds its wheel from a checkout, so the macOS DMG
probably ships without the baked YouTube OAuth client, and "Connect
YouTube" would have no client in the released app. Unverified; check a
built DMG's `python/lib/python3.12/site-packages/splitsmith/youtube/oauth.py`.
If confirmed, `release.sh` should pass the PyPI wheel through the same
`--wheel` option this spec adds. Tracked separately so this spec stays
Linux-only.
