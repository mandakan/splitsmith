# Desktop app on Linux (AppImage + .deb) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an x86_64 AppImage and .deb of the Electron desktop app on every `vX.Y.Z` GitHub release, built in CI, without changing the macOS app, hosted, or uvx.

**Architecture:** The same shell as macOS (Electron + python-build-standalone + the wheel + our static ffmpeg), parameterised by a build *target* (`macos-aarch64` | `linux-x86_64`) that every build script resolves through one helper. Linux-only behaviour in the Electron main process is decided by pure, vitest-tested modules (`sidecar.ts`, `menuTemplate.ts`, `sandbox.ts`, `updateCheck.ts`). A reusable workflow builds the Linux artifacts in an `ubuntu:22.04` container from the wheel PyPI already published, and uploads them to the release.

**Tech Stack:** Electron 44, electron-builder 26.15.3, TypeScript, vitest, bash, uv, python-build-standalone 3.12, static ffmpeg 9.0.2, GitHub Actions, Cloudflare Pages Functions.

**Spec:** `docs/superpowers/specs/2026-10-02-desktop-linux-packaging-design.md`

## Global Constraints

- macOS behaviour is byte-identical: every function's `darwin` output, every script's default on a macOS host, and the feed's no-parameter answer are unchanged. A diff that edits a macOS branch is a review finding.
- No Python changes under `src/`. (`scripts/ci/` helpers are fine.)
- No new runtime or dev dependencies in `desktop/package.json` or `pyproject.toml`.
- Linux target: `x86_64` only, glibc floor 2.35 (`ubuntu:22.04` build container).
- Python build: the baseline `cpython-3.12-linux-x86_64-gnu`, never a `_v2/_v3/_v4` variant.
- `SPLITSMITH_HOME` is never set by the shell; Linux data lives in `~/.config/splitsmith`, shared with uvx.
- Never a silent `--no-sandbox`: the AppImage refuses with a dialog where the sandbox cannot start.
- The .deb's postinst/postrm must keep electron-builder's stock content (chrome-sandbox, AppArmor).
- Inside `desktop/linux/*.sh` (electron-builder templates), never write `${letters}` for a shell variable: electron-builder replaces `/\${([a-zA-Z]+)}/` and throws on unknown names. Use `$VAR` or names with an underscore (`${SPLITSMITH_BIN_DIR}`).
- Prose in code, docs and commits: ASCII punctuation only (`--`, `...`, straight quotes).
- Use `corepack pnpm` if `pnpm` is not on PATH. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A macOS build after this branch.** A macOS host running `desktop/build.sh` with no flags must run exactly today's steps. Pinned by `src/buildTarget.test.ts` (Task 6: host `Darwin arm64` resolves `macos-aarch64`, a Linux host resolves `linux-x86_64`, anything else fails) and by the darwin cases in Tasks 2-4.
2. **XDG variables that are empty or relative** must fall back to the home-relative default, not produce a relative log dir. Pinned in Task 3.
3. **The .deb over an existing `/usr/bin/splitsmith` that is not ours**, and a reinstall or upgrade over our own link. postinst must leave a foreign file alone and be idempotent; postrm must remove only a link into `/opt/Splitsmith`. Pinned in Task 8 by running the appended snippet against a temp dir.
4. **A release with only some assets attached.** The DMG alone must not announce to Linux; one of AppImage/.deb must not announce to Linux; Linux assets alone must not announce to macOS. Pinned in Task 1.
5. **PyPI not serving the new version yet** when the release job starts (CDN lag), or serving no `py3-none-any` wheel. The fetch retries and then fails loudly; it never falls back to a checkout-built wheel. Pinned in Task 9's pytest.

---

### Task 1: Feed picks per platform; desktop unit tests run in CI

`desktop/src/updateFeed.test.ts` is red on main today: its fixtures carry no `.dmg`, which #1098 made mandatory, and no CI job runs the desktop vitest suite. This task fixes both and adds the platform split.

**Files:**
- Modify: `functions/desktop/latest.json.js`
- Modify: `desktop/src/updateFeed.test.ts`
- Modify: `.github/workflows/ci.yml` (new `desktop-unit` job after `spa`)

**Interfaces:**
- Produces: `pickLatest(releases, platform = "mac")` where `platform` is `"mac" | "linux"`; `GET /desktop/latest.json?platform=linux`.

- [ ] **Step 1: Confirm the existing failure**

Run: `cd desktop && corepack pnpm install --frozen-lockfile && corepack pnpm exec vitest run src/updateFeed.test.ts`
Expected: FAIL, `pickLatest > skips non-app tags...` receives `null`.

- [ ] **Step 2: Rewrite the test file**

```ts
/**
 * The Pages Function behind https://splitsmith.app/desktop/latest.json
 * lives in functions/ (no test runner there); its release picking is
 * tested here since the app is its only consumer.
 */
import { describe, expect, it } from "vitest";

// @ts-expect-error plain JS module without types
import { onRequestGet, pickLatest } from "../../functions/desktop/latest.json.js";

const DMG = "Splitsmith-0.0.0-arm64.dmg";
const APPIMAGE = "Splitsmith-0.0.0-x86_64.AppImage";
const DEB = "splitsmith-desktop_0.0.0_amd64.deb";

const rel = (
  tag_name: string,
  assets: string[] = [DMG],
  extra: Partial<{ draft: boolean; prerelease: boolean }> = {},
) => ({
  tag_name,
  html_url: `https://github.com/mandakan/splitsmith/releases/tag/${tag_name}`,
  draft: false,
  prerelease: false,
  assets: assets.map((name) => ({ name })),
  ...extra,
});
const at = (tag: string) => ({ version: tag.slice(1), url: `https://github.com/mandakan/splitsmith/releases/tag/${tag}` });

describe("pickLatest", () => {
  it("skips non-app tags, drafts and prereleases, and orders numerically", () => {
    const picked = pickLatest([
      rel("ffmpeg-macos-arm64-9.0.2-r1"),
      rel("v0.40.1"),
      rel("v0.41.0", [DMG], { draft: true }),
      rel("v0.42.0", [DMG], { prerelease: true }),
      rel("v0.9.9"),
      rel("v0.40.0"),
    ]);
    expect(picked).toEqual(at("v0.40.1"));
  });
  it("is null with no app release", () => {
    expect(pickLatest([rel("ffmpeg-macos-arm64-9.0.2-r1")])).toBeNull();
    expect(pickLatest([])).toBeNull();
  });
  it("defaults to macOS: a release counts once its DMG is attached", () => {
    expect(pickLatest([rel("v0.43.0", [APPIMAGE, DEB]), rel("v0.42.0", [DMG])])).toEqual(at("v0.42.0"));
  });
  it("linux needs both the AppImage and the deb", () => {
    const releases = [rel("v0.44.0", [DMG]), rel("v0.43.0", [DMG, APPIMAGE]), rel("v0.42.0", [DMG, APPIMAGE, DEB])];
    expect(pickLatest(releases, "linux")).toEqual(at("v0.42.0"));
    expect(pickLatest(releases, "mac")).toEqual(at("v0.44.0"));
  });
  it("rejects an unknown platform", () => {
    expect(() => pickLatest([], "windows")).toThrow();
  });
});

describe("onRequestGet", () => {
  it("answers 400 for an unknown platform without calling GitHub", async () => {
    const res = await onRequestGet({ request: new Request("https://splitsmith.app/desktop/latest.json?platform=win") });
    expect(res.status).toBe(400);
  });
});
```

- [ ] **Step 3: Run it to see the new cases fail**

Run: `cd desktop && corepack pnpm exec vitest run src/updateFeed.test.ts`
Expected: the first two cases PASS (fixtures fixed), the linux, unknown-platform and `onRequestGet` cases FAIL.

- [ ] **Step 4: Implement in `functions/desktop/latest.json.js`**

Replace the header's "only once a .dmg is attached" sentence with the per-platform rule, and the picking and handler with:

```js
// Which assets must be attached before a release is announced to an app
// on that platform. Shipped macOS apps send no ?platform=, so "mac" is
// the default and its rule never changes.
const REQUIRED_ASSETS = {
  mac: [/\.dmg$/],
  linux: [/\.AppImage$/, /\.deb$/],
};

export function pickLatest(releases, platform = "mac") {
  const required = REQUIRED_ASSETS[platform];
  if (!required) throw new Error(`unknown platform ${platform}`);
  const versions = [];
  for (const r of releases) {
    if (r.draft || r.prerelease) continue;
    const m = APP_TAG.exec(r.tag_name || "");
    if (!m) continue;
    const names = (r.assets || []).map((a) => a.name || "");
    if (!required.every((re) => names.some((n) => re.test(n)))) continue;
    versions.push({ version: m[1], url: r.html_url, key: m[1].split(".").map(Number) });
  }
  versions.sort((a, b) => b.key[0] - a.key[0] || b.key[1] - a.key[1] || b.key[2] - a.key[2]);
  return versions.length ? { version: versions[0].version, url: versions[0].url } : null;
}

export async function onRequestGet({ request }) {
  const requested = new URL(request.url).searchParams.get("platform");
  if (requested !== null && requested !== "linux") return json(400, { error: `unknown platform ${requested}` });
  const platform = requested ?? "mac";
  let releases;
  // ... the existing fetch block, unchanged ...
  const latest = pickLatest(Array.isArray(releases) ? releases : [], platform);
  if (!latest) return json(404, { error: "no app release" });
  return json(200, latest, { "cache-control": `public, max-age=${CACHE_SECONDS}` });
}
```

- [ ] **Step 5: Run the whole desktop suite**

Run: `cd desktop && corepack pnpm exec vitest run && corepack pnpm typecheck`
Expected: all PASS.

- [ ] **Step 6: Prove the linux test can fail**

Temporarily change `linux: [/\.AppImage$/, /\.deb$/]` to `linux: [/\.AppImage$/]`, rerun the file, confirm "linux needs both" FAILS, revert.

- [ ] **Step 7: Add the CI job** in `.github/workflows/ci.yml`, after the `spa` job, copying its node/pnpm setup lines exactly:

```yaml
  desktop-unit:
    # The Electron shell's pure modules and the update feed's picking.
    # Cheap and always on: the feed lives in functions/, outside
    # desktop.yml's path filter, and went red unnoticed once (#1098).
    name: desktop unit tests
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: desktop
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - uses: pnpm/action-setup@v4
      - run: pnpm install --frozen-lockfile
      - run: pnpm typecheck
      - run: pnpm test
```

- [ ] **Step 8: Commit**

```bash
git add functions/desktop/latest.json.js desktop/src/updateFeed.test.ts .github/workflows/ci.yml
git commit -m "feat(desktop): the update feed answers per platform; desktop unit tests run in CI"
```

---

### Task 2: The app asks the feed for its own platform

**Files:**
- Modify: `desktop/src/updateCheck.ts`, `desktop/src/updateCheck.test.ts`, `desktop/src/updates.ts`

**Interfaces:**
- Consumes: `?platform=linux` from Task 1.
- Produces: `feedUrl(platform: NodeJS.Platform): string` in `updateCheck.ts`.

- [ ] **Step 1: Failing test** (append to `updateCheck.test.ts`, add `feedUrl, UPDATE_FEED_URL` to the import)

```ts
describe("feedUrl", () => {
  it("is unchanged on macOS and asks for linux on Linux", () => {
    expect(feedUrl("darwin")).toBe(UPDATE_FEED_URL);
    expect(feedUrl("darwin")).toBe("https://splitsmith.app/desktop/latest.json");
    expect(feedUrl("linux")).toBe("https://splitsmith.app/desktop/latest.json?platform=linux");
  });
});
```

- [ ] **Step 2:** `cd desktop && corepack pnpm exec vitest run src/updateCheck.test.ts` -- FAIL, `feedUrl` not exported.

- [ ] **Step 3: Implement** in `updateCheck.ts` after `FEED_TIMEOUT_MS`:

```ts
/** Shipped macOS apps ask with no parameter; that URL never changes. */
export function feedUrl(platform: NodeJS.Platform): string {
  return platform === "linux" ? `${UPDATE_FEED_URL}?platform=linux` : UPDATE_FEED_URL;
}
```

In `updates.ts`, import `feedUrl as platformFeedUrl` instead of `UPDATE_FEED_URL`, and make the local function:

```ts
function feedUrl(): string {
  // Dev override so the sheet can be exercised against a local feed.
  return process.env.SPLITSMITH_UPDATE_FEED ?? platformFeedUrl(process.platform);
}
```

- [ ] **Step 4:** `corepack pnpm exec vitest run && corepack pnpm typecheck` -- PASS.

- [ ] **Step 5: Commit** `feat(desktop): the update check names its platform`

---

### Task 3: Sidecar paths per platform

**Files:**
- Modify: `desktop/src/sidecar.ts`, `desktop/src/sidecar.test.ts`, `desktop/src/main.ts:52`

**Interfaces:**
- Produces: `SidecarOptions.platform: NodeJS.Platform` (required); `platformDirs(platform, home, env): { logDir: string; numbaCacheDir: string }` exported from `sidecar.ts`.

- [ ] **Step 1: Failing tests.** In `sidecar.test.ts` add `platform: "darwin"` to the existing `sidecarSpec({...})` call (its expectations stay exactly as they are -- that is the macOS pin), import `platformDirs`, and append:

```ts
describe("sidecarSpec on Linux", () => {
  const base = { resourcesPath: "/opt/Splitsmith/resources", port: 5000, home: "/home/me" };
  it("logs and caches under the XDG defaults", () => {
    const spec = sidecarSpec({ ...base, platform: "linux", env: { PATH: "/usr/bin" } });
    expect(spec.command).toBe("/opt/Splitsmith/resources/python/bin/python3.12");
    expect(spec.logDir).toBe("/home/me/.local/state/splitsmith/logs");
    expect(spec.args).toEqual(["-m", "splitsmith.ui.embedded", "--log-dir", "/home/me/.local/state/splitsmith/logs"]);
    expect(spec.env.NUMBA_CACHE_DIR).toBe("/home/me/.cache/splitsmith/numba");
    expect(spec.env.SPLITSMITH_FFMPEG).toBe("/opt/Splitsmith/resources/bin/ffmpeg");
    expect(spec.env).not.toHaveProperty("SPLITSMITH_HOME");
  });
  it("honours absolute XDG variables", () => {
    const spec = sidecarSpec({
      ...base,
      platform: "linux",
      env: { XDG_STATE_HOME: "/x/state", XDG_CACHE_HOME: "/x/cache" },
    });
    expect(spec.logDir).toBe("/x/state/splitsmith/logs");
    expect(spec.env.NUMBA_CACHE_DIR).toBe("/x/cache/splitsmith/numba");
  });
  it("ignores empty and relative XDG variables", () => {
    for (const bad of ["", "relative/state", "~/state"]) {
      const d = platformDirs("linux", "/home/me", { XDG_STATE_HOME: bad, XDG_CACHE_HOME: bad });
      expect(d).toEqual({
        logDir: "/home/me/.local/state/splitsmith/logs",
        numbaCacheDir: "/home/me/.cache/splitsmith/numba",
      });
    }
  });
  it("still drops a SPLITSMITH_HOME from the environment", () => {
    const spec = sidecarSpec({ ...base, platform: "linux", env: { SPLITSMITH_HOME: "/elsewhere" } });
    expect(spec.env).not.toHaveProperty("SPLITSMITH_HOME");
  });
});

describe("platformDirs on macOS", () => {
  it("ignores XDG variables", () => {
    expect(platformDirs("darwin", "/Users/me", { XDG_STATE_HOME: "/x" })).toEqual({
      logDir: "/Users/me/Library/Logs/Splitsmith",
      numbaCacheDir: "/Users/me/Library/Caches/Splitsmith/numba",
    });
  });
});
```

- [ ] **Step 2:** `corepack pnpm exec vitest run src/sidecar.test.ts` -- FAIL (`platformDirs` missing; typecheck of `platform` too).

- [ ] **Step 3: Implement** in `sidecar.ts`:

```ts
export interface SidecarOptions {
  /** ``Contents/Resources`` of the app bundle (or a dev stand-in). */
  resourcesPath: string;
  port: number;
  home: string;
  env: NodeJS.ProcessEnv;
  /** ``process.platform``; passed in so this stays pure. */
  platform: NodeJS.Platform;
}

/** An XDG base dir only when it is absolute, as the spec requires (and user_config.py does). */
function xdgBase(env: NodeJS.ProcessEnv, name: string, fallback: string): string {
  const v = env[name];
  return v && path.isAbsolute(v) ? v : fallback;
}

/**
 * Where the sidecar logs and where numba caches. macOS keeps the paths
 * it shipped with; Linux follows XDG, next to the engine's own cache dir
 * (runtime._platform_cache_dir). Data (SPLITSMITH_HOME) is never set:
 * the engine picks the same place the CLI does.
 */
export function platformDirs(
  platform: NodeJS.Platform,
  home: string,
  env: NodeJS.ProcessEnv,
): { logDir: string; numbaCacheDir: string } {
  if (platform === "linux") {
    const state = xdgBase(env, "XDG_STATE_HOME", path.join(home, ".local", "state"));
    const cache = xdgBase(env, "XDG_CACHE_HOME", path.join(home, ".cache"));
    return { logDir: path.join(state, "splitsmith", "logs"), numbaCacheDir: path.join(cache, "splitsmith", "numba") };
  }
  return {
    logDir: path.join(home, "Library", "Logs", "Splitsmith"),
    numbaCacheDir: path.join(home, "Library", "Caches", "Splitsmith", "numba"),
  };
}
```

In `sidecarSpec`, destructure `platform`, replace the `logDir` line with `const { logDir, numbaCacheDir } = platformDirs(platform, home, env);`, and set `NUMBA_CACHE_DIR: numbaCacheDir` (keep its comment, reworded: "The bundle is read-only (signed on macOS, squashfs in an AppImage); numba's default cache is next to the module and would fail to write."). In `main.ts` `startSidecar`, pass `platform: process.platform`.

- [ ] **Step 4:** `corepack pnpm exec vitest run && corepack pnpm typecheck` -- PASS.

- [ ] **Step 5: Commit** `feat(desktop): sidecar logs and numba cache follow XDG on Linux`

---

### Task 4: Menu template per platform

**Files:**
- Create: `desktop/src/menuTemplate.ts`, `desktop/src/menuTemplate.test.ts`
- Modify: `desktop/src/menu.ts` (`buildMenu` only)

**Interfaces:**
- Produces: `interface MenuHandlers { showAbout(): void; showNotices(): void; checkForUpdates(): void; installCli(): void; openLogFolder(): void }` and `menuTemplate(platform: NodeJS.Platform, h: MenuHandlers): Electron.MenuItemConstructorOptions[]`.

- [ ] **Step 1: Failing test** `menuTemplate.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { menuTemplate, type MenuHandlers } from "./menuTemplate";

const noop = () => undefined;
const h: MenuHandlers = { showAbout: noop, showNotices: noop, checkForUpdates: noop, installCli: noop, openLogFolder: noop };

type Item = { label?: string; role?: string; type?: string; submenu?: Item[] };
const shape = (items: Item[]): unknown =>
  items.map((i) => (i.submenu ? { [i.label ?? i.role ?? "?"]: shape(i.submenu) } : (i.label ?? i.role ?? i.type)));

describe("menuTemplate", () => {
  it("keeps the macOS menu exactly as it shipped", () => {
    expect(shape(menuTemplate("darwin", h, "Splitsmith") as Item[])).toEqual([
      {
        Splitsmith: [
          "About Splitsmith", "Third-party notices", "Check for updates...", "separator",
          "Install command line tool", "separator", "hide", "hideOthers", "unhide", "separator", "quit",
        ],
      },
      { File: ["Open log folder", "close"] },
      "editMenu",
      { View: ["reload", "resetZoom", "zoomIn", "zoomOut", "separator", "togglefullscreen", "toggleDevTools"] },
      "windowMenu",
    ]);
  });
  it("on Linux has no app menu, no CLI installer, and Help carries About and updates", () => {
    expect(shape(menuTemplate("linux", h, "Splitsmith") as Item[])).toEqual([
      { File: ["Open log folder", "separator", "quit"] },
      "editMenu",
      { View: ["reload", "resetZoom", "zoomIn", "zoomOut", "separator", "togglefullscreen", "toggleDevTools"] },
      { Help: ["About Splitsmith", "Third-party notices", "Check for updates..."] },
    ]);
  });
});
```

`appName` is `app.name` at runtime (`"Splitsmith"`, the productName); passing it keeps the module free of Electron values.

- [ ] **Step 2:** `corepack pnpm exec vitest run src/menuTemplate.test.ts` -- FAIL, module missing.

- [ ] **Step 3: Implement** `menuTemplate.ts` (type-only Electron import, so it runs under vitest):

```ts
/**
 * The application menu's shape, per platform. Pure: menu.ts supplies the
 * handlers and applies it. macOS keeps the menu it shipped with; Linux
 * has no app-name menu (hide/hideOthers/unhide are macOS roles) and no
 * CLI installer (the .deb installs /usr/bin/splitsmith).
 */
import type { MenuItemConstructorOptions } from "electron";

export interface MenuHandlers {
  showAbout(): void;
  showNotices(): void;
  checkForUpdates(): void;
  installCli(): void;
  openLogFolder(): void;
}

const VIEW: MenuItemConstructorOptions = {
  label: "View",
  submenu: [
    { role: "reload" },
    { role: "resetZoom" },
    { role: "zoomIn" },
    { role: "zoomOut" },
    { type: "separator" },
    { role: "togglefullscreen" },
    { role: "toggleDevTools" },
  ],
};

export function menuTemplate(platform: NodeJS.Platform, h: MenuHandlers, appName: string): MenuItemConstructorOptions[] {
  if (platform === "linux") {
    return [
      { label: "File", submenu: [{ label: "Open log folder", click: h.openLogFolder }, { type: "separator" }, { role: "quit" }] },
      { role: "editMenu" },
      VIEW,
      {
        label: "Help",
        submenu: [
          { label: "About Splitsmith", click: h.showAbout },
          { label: "Third-party notices", click: h.showNotices },
          { label: "Check for updates...", click: h.checkForUpdates },
        ],
      },
    ];
  }
  return [
    {
      label: appName,
      submenu: [
        { label: "About Splitsmith", click: h.showAbout },
        { label: "Third-party notices", click: h.showNotices },
        { label: "Check for updates...", click: h.checkForUpdates },
        { type: "separator" },
        { label: "Install command line tool", click: h.installCli },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { label: "File", submenu: [{ label: "Open log folder", click: h.openLogFolder }, { role: "close" }] },
    { role: "editMenu" },
    VIEW,
    { role: "windowMenu" },
  ];
}
```

Replace `buildMenu` in `menu.ts`:

```ts
export function buildMenu(): void {
  const template = menuTemplate(
    process.platform,
    {
      showAbout,
      showNotices,
      checkForUpdates: () => void checkForUpdates({ interactive: true }),
      installCli,
      openLogFolder: () => void shell.openPath(sidecarState.logDir),
    },
    app.name,
  );
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
```

Also on Linux, `parentWindow()` returning null makes About silent when no window exists; that is fine (a window always exists while the menu is reachable on Linux, the menu lives in it).

- [ ] **Step 4:** `corepack pnpm exec vitest run && corepack pnpm typecheck` -- PASS.

- [ ] **Step 5: Commit** `feat(desktop): Linux menu without the macOS app menu or CLI installer`

---

### Task 5: AppImage sandbox refusal and the window icon

**Files:**
- Create: `desktop/src/sandbox.ts`, `desktop/src/sandbox.test.ts`
- Modify: `desktop/src/main.ts`

**Interfaces:**
- Produces: `sandboxBlocked(o: { appImage: boolean; noSandbox: boolean; userNsWorks: () => boolean }): boolean`; `RELEASES_PAGE` constant; `BLOCKED_MESSAGE` / `BLOCKED_DETAIL` strings.
- Consumes: `appImage.executableArgs: []` from Task 8 (without it the desktop entry passes `--no-sandbox` everywhere; the check still refuses only where namespaces fail).

- [ ] **Step 1: Failing test** `sandbox.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { sandboxBlocked } from "./sandbox";

describe("sandboxBlocked", () => {
  const cases: Array<[boolean, boolean, boolean, boolean]> = [
    // appImage, noSandbox, userNsWorks, blocked
    [true, true, false, true], // AppRun fell back: refuse
    [true, true, true, false], // user passed --no-sandbox where it works: their call
    [true, false, false, false], // sandbox on: Electron itself decides
    [true, false, true, false],
    [false, true, false, false], // .deb / dev run: never ours to refuse
    [false, true, true, false],
    [false, false, false, false],
    [false, false, true, false],
  ];
  it.each(cases)("appImage=%s noSandbox=%s userNs=%s -> %s", (appImage, noSandbox, works, blocked) => {
    expect(sandboxBlocked({ appImage, noSandbox, userNsWorks: () => works })).toBe(blocked);
  });
  it("only probes namespaces when the other two hold", () => {
    let probed = 0;
    const probe = () => {
      probed += 1;
      return false;
    };
    sandboxBlocked({ appImage: false, noSandbox: true, userNsWorks: probe });
    sandboxBlocked({ appImage: true, noSandbox: false, userNsWorks: probe });
    expect(probed).toBe(0);
  });
});
```

- [ ] **Step 2:** run it -- FAIL, module missing.

- [ ] **Step 3: Implement** `sandbox.ts`:

```ts
/**
 * The AppImage's sandbox refusal, pure part. electron-builder's AppRun
 * adds --no-sandbox when ``unshare -Ur true`` fails (Ubuntu 24.04+ with
 * AppArmor's userns restriction); left alone the app would then run
 * unsandboxed without saying so. main.ts turns that case into a dialog
 * and a quit. A .deb run ($APPIMAGE unset) is never refused: its setuid
 * helper and AppArmor profile carry the sandbox.
 */
export const RELEASES_PAGE = "https://github.com/mandakan/splitsmith/releases";
export const BLOCKED_MESSAGE = "This system blocks the sandbox an AppImage needs";
export const BLOCKED_DETAIL =
  "Install the .deb from the release page instead. It sets the sandbox up during installation.";

export function sandboxBlocked(o: { appImage: boolean; noSandbox: boolean; userNsWorks: () => boolean }): boolean {
  return o.appImage && o.noSandbox && !o.userNsWorks();
}
```

In `main.ts`: import `spawnSync` from `node:child_process`, `fs` from `node:fs`, and the four names from `./sandbox`. Add:

```ts
function userNsWorks(): boolean {
  return spawnSync("unshare", ["-Ur", "true"], { stdio: "ignore", timeout: 3_000 }).status === 0;
}

/** A dialog and a quit; never a window, never the sidecar, never web content. */
async function refuseUnsandboxed(): Promise<void> {
  const { response } = await dialog.showMessageBox({
    type: "error",
    message: BLOCKED_MESSAGE,
    detail: BLOCKED_DETAIL,
    buttons: ["Open release page", "Quit"],
    defaultId: 0,
    cancelId: 1,
  });
  if (response === 0) await shell.openExternal(RELEASES_PAGE);
  app.quit();
}

/** The window icon on Linux; macOS takes it from the bundle. */
function windowIcon(): string | undefined {
  if (process.platform !== "linux") return undefined;
  const icon = path.join(resourcesPath(), "icon.png");
  return fs.existsSync(icon) ? icon : undefined;
}
```

In `createWindow`, add `icon: windowIcon(),` to the `BrowserWindow` options. Change the `whenReady` body to:

```ts
  void app.whenReady().then(() => {
    const blocked = sandboxBlocked({
      appImage: Boolean(process.env.APPIMAGE),
      noSandbox: app.commandLine.hasSwitch("no-sandbox"),
      userNsWorks,
    });
    if (blocked) {
      void refuseUnsandboxed();
      return;
    }
    buildMenu();
    createWindow();
    startSidecar().catch((err: Error) => showFailure([String(err.message)]));
  });
```

`before-quit` calls `stopSidecar()`, which returns at once with no child, so quitting from the refusal is clean.

- [ ] **Step 4:** `corepack pnpm exec vitest run && corepack pnpm typecheck` -- PASS.

- [ ] **Step 5: Commit** `feat(desktop): an AppImage that cannot sandbox refuses with a dialog`

---

### Task 6: Build target helper and the Linux Python runtime

**Files:**
- Create: `desktop/lib/target.sh`, `desktop/src/buildTarget.test.ts`
- Modify: `desktop/build-runtime.sh`

**Interfaces:**
- Produces: `host_target` (prints `macos-aarch64` | `linux-x86_64`, exit 1 otherwise), `parse_target_flag <arg>` (`--mac` -> `macos-aarch64`, `--linux` -> `linux-x86_64`), `sha256_verify <sha> <file>`; `build-runtime.sh [--target T] [--wheel PATH]`.

- [ ] **Step 1: Failing test** `buildTarget.test.ts` (fakes `uname` on PATH):

```ts
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

const LIB = path.join(__dirname, "..", "lib", "target.sh");

function hostTarget(s: string, m: string): { code: number | null; out: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "uname-"));
  fs.writeFileSync(path.join(dir, "uname"), `#!/bin/sh\n[ "$1" = -s ] && echo ${s} || echo ${m}\n`, { mode: 0o755 });
  const r = spawnSync("bash", ["-c", `source '${LIB}' && host_target`], {
    env: { PATH: `${dir}:/usr/bin:/bin` },
    encoding: "utf8",
  });
  return { code: r.status, out: r.stdout.trim() };
}

describe("host_target", () => {
  it("is macos-aarch64 on an arm64 Mac, so a flagless macOS build is unchanged", () => {
    expect(hostTarget("Darwin", "arm64")).toEqual({ code: 0, out: "macos-aarch64" });
  });
  it("is linux-x86_64 on x86_64 Linux", () => {
    expect(hostTarget("Linux", "x86_64")).toEqual({ code: 0, out: "linux-x86_64" });
  });
  it("refuses anything else", () => {
    expect(hostTarget("Darwin", "x86_64").code).toBe(1);
    expect(hostTarget("Linux", "aarch64").code).toBe(1);
  });
});
```

- [ ] **Step 2:** run it -- FAIL, `lib/target.sh` missing.

- [ ] **Step 3: Create `desktop/lib/target.sh`:**

```bash
# shellcheck shell=bash
# Build targets, shared by the desktop build scripts. Sourced, not run.
#   macos-aarch64  the signed DMG (the only target before Linux shipped)
#   linux-x86_64   the AppImage and .deb

host_target() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) echo macos-aarch64 ;;
    Linux-x86_64) echo linux-x86_64 ;;
    *) echo "unsupported build host $(uname -s)-$(uname -m)" >&2; return 1 ;;
  esac
}

parse_target_flag() {
  case "$1" in
    --mac) echo macos-aarch64 ;;
    --linux) echo linux-x86_64 ;;
    *) return 1 ;;
  esac
}

# sha256_verify <sha256> <file>: shasum on macOS, sha256sum on Linux.
sha256_verify() {
  if command -v sha256sum >/dev/null; then
    echo "$1  $2" | sha256sum -c - >/dev/null
  else
    echo "$1  $2" | shasum -a 256 -c - >/dev/null
  fi
}
```

- [ ] **Step 4:** run the test -- PASS.

- [ ] **Step 5: Parameterise `build-runtime.sh`.** Replace the lines from `wheel=` through `mv "$src" "$RUNTIME/python"` with:

```bash
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
  *) echo "unknown target $TARGET" >&2; exit 2 ;;
esac
rm -rf "$RUNTIME"; mkdir -p "$RUNTIME"
UV_PYTHON_INSTALL_DIR="$RUNTIME" uv python install "$request"
src="$(ls -d "$RUNTIME"/cpython-"$PYVER".*-"$layout")"
mv "$src" "$RUNTIME/python"
```

Everything after stays. Check the pruning globs work on Linux (`lib/python3.12/lib-dynload/_tkinter*.so`, `lib/libtcl*`, `lib/libtk*` exist there too; `rm -rf` of a missing path is harmless).

- [ ] **Step 6: Run it on this Linux host** (gaspode, not in a container: this proves the script, Task 10 proves the container):

```bash
cd ~/work/splitsmith && rm -rf dist && uv build --wheel
desktop/build-runtime.sh --target linux-x86_64
desktop/build/runtime/python/bin/python3.12 -c "import splitsmith, platform; print(splitsmith.__version__, platform.machine())"
head -3 desktop/build/runtime/python/bin/splitsmith
desktop/build/runtime/python/bin/splitsmith --version
```

Expected: version prints, `x86_64`, the shim's `'''exec'` header, the CLI runs. Then `git checkout -- uv.lock` if `uv build` touched it.

- [ ] **Step 7: Commit** `build(desktop): build-runtime takes a target and a wheel; Linux uses baseline x86_64 Python`

---

### Task 7: Static Linux ffmpeg

**Files:**
- Modify: `desktop/build-ffmpeg.sh`, `desktop/fetch-ffmpeg.sh`, `desktop/build-notices.sh`
- Create: `desktop/build-ffmpeg-linux.sh`

**Interfaces:**
- Consumes: `lib/target.sh` (Task 6).
- Produces: `fetch-ffmpeg.sh [--target T]` writes `build/bin/{ffmpeg,ffprobe,FFMPEG_RELEASE}`; `FFMPEG_RELEASE` holds the release tag, read by `build-notices.sh`.

- [ ] **Step 1: Linux branch in `build-ffmpeg.sh`.** Keep every macOS line; branch on `OS="$(uname -s)"` at these points only:
  - tool check: macOS keeps `glibtoolize` and the brew hint; Linux checks `libtoolize` with hint `apt-get install nasm pkg-config meson ninja-build autoconf automake libtool`.
  - the `arm64 host required` check runs only on Darwin; Linux requires `x86_64`.
  - `MACOSX_DEPLOYMENT_TARGET` and `JOBS="$(sysctl -n hw.ncpu)"` on Darwin; `JOBS="$(nproc)"` on Linux.
  - the configure array: the shared part stays; the platform part is

```bash
if [ "$OS" = Darwin ]; then
  CONFIGURE+=(--enable-videotoolbox --enable-audiotoolbox --enable-avfoundation --enable-coreimage
    --extra-ldflags="-L$PREFIX/lib" --extra-cflags="-I$PREFIX/include"
    --extra-libs="-lc++ -liconv -lbz2 -lz")
else
  # zlib and bz2 static from the distro: their .a files are copied into
  # $PREFIX/lib, which ld searches before the system dirs, so ld finds the
  # archive there before it ever sees libz.so. libstdc++ (harfbuzz) by
  # file name for the same reason.
  cp /usr/lib/x86_64-linux-gnu/libz.a /usr/lib/x86_64-linux-gnu/libbz2.a "$PREFIX/lib/"
  CONFIGURE+=(--extra-ldflags="-L$PREFIX/lib -static-libgcc" --extra-cflags="-I$PREFIX/include"
    --extra-libs="-l:libstdc++.a -lbz2 -lz -lm -lpthread")
fi
```

  (`--enable-zlib --enable-bzlib --enable-iconv` stay shared; glibc provides iconv.) Move the shared flags that are macOS-only today (`--enable-videotoolbox` etc.) out of the shared array into the Darwin branch; the resulting macOS argv must be the same flags in the same order -- diff `BUILD-RECIPE.txt` logic by reading, since this host cannot run the macOS branch.
  - verify: Darwin keeps the `otool -L` check; Linux uses

```bash
    allowed='linux-vdso|libc\.so|libm\.so|libmvec\.so|libpthread\.so|libdl\.so|librt\.so|ld-linux-x86-64'
    if ldd "$OUT/$bin" | grep -v -E "$allowed"; then echo "$bin links a non-glibc library" >&2; exit 1; fi
```

  - the `h264_videotoolbox` assertion runs only on Darwin.
  - the tarball name: `ffmpeg-macos-arm64-...` on Darwin, `ffmpeg-linux-x86_64-$FFVER-$VARIANT.tar.gz` on Linux.

- [ ] **Step 2: Create `desktop/build-ffmpeg-linux.sh`** (Docker wrapper, so the binary links against the 22.04 glibc floor):

```bash
#!/usr/bin/env bash
# Build the Linux ffmpeg inside ubuntu:22.04 (glibc 2.35, the app's floor).
# Same flags as build-ffmpeg.sh; output lands in desktop/build/ as on macOS.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
docker run --rm -v "$HERE:/desktop" -w /desktop ubuntu:22.04 bash -c '
  set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq build-essential nasm pkg-config meson ninja-build autoconf automake libtool \
    curl xz-utils bzip2 zlib1g-dev libbz2-dev ca-certificates >/dev/null
  ./build-ffmpeg.sh "$@"
  chown -R '"$(id -u):$(id -g)"' build
' -- "$@"
```

- [ ] **Step 3: Build it** on gaspode (Docker available; serialise with other heavy jobs on the box):

Run: `desktop/build-ffmpeg-linux.sh`
Expected: `built .../build/ffmpeg-linux-x86_64-9.0.2-gpl.tar.gz`, no `links a non-glibc library`. Then `tar -xzf` it into a temp dir and run `./ffmpeg -hide_banner -filters | grep drawtext` and `-encoders | grep libx264` on the host and in a `debian:12` container (`docker run --rm -v ...:/f debian:12 /f/ffmpeg -version`).

- [ ] **Step 4: Publish the ffmpeg release -- STOP and ask the user first** (outward-facing). On approval:

```bash
gh release create ffmpeg-linux-x86_64-9.0.2-r1 desktop/build/ffmpeg-linux-x86_64-9.0.2-gpl.tar.gz \
  desktop/build/ffmpeg-src/*.tar.* --title "ffmpeg 9.0.2 for the Linux desktop app (GPL)" \
  --notes "Static ffmpeg + ffprobe for x86_64 Linux, built by desktop/build-ffmpeg-linux.sh. Sources attached." \
  --latest=false
sha256sum desktop/build/ffmpeg-linux-x86_64-9.0.2-gpl.tar.gz
```

Mirror the macOS ffmpeg release's asset list (`gh release view ffmpeg-macos-arm64-9.0.2-r1`) for which sources to attach. `--latest=false` matters: `releases/latest` must not become an ffmpeg tag.

- [ ] **Step 5: `fetch-ffmpeg.sh` per target.** Replace the `TAG`/`ASSET`/`SHA256` lines and the verify lines:

```bash
# shellcheck source=lib/target.sh
source "$HERE/lib/target.sh"
TARGET="$(host_target)"
[ "${1:-}" = --target ] && TARGET="$2"
case "$TARGET" in
  macos-aarch64)
    TAG="ffmpeg-macos-arm64-9.0.2-r1"
    ASSET="ffmpeg-macos-arm64-9.0.2-gpl.tar.gz"
    SHA256="a7e2482a55deeb430170661766d28c5666accad2e8710794d15a621cb6798e66" ;;
  linux-x86_64)
    TAG="ffmpeg-linux-x86_64-9.0.2-r1"
    ASSET="ffmpeg-linux-x86_64-9.0.2-gpl.tar.gz"
    SHA256="<the sha256sum printed in Step 4>" ;;
  *) echo "unknown target $TARGET" >&2; exit 2 ;;
esac
```

Both `shasum -a 256 -c` calls become `sha256_verify "$SHA256" "$tarball"` (the first one inside the existing `if ... && ! ...` form). After extracting, `echo "$TAG" > "$DEST/FFMPEG_RELEASE"`.

In `build-notices.sh`, replace the `release_url=` line with:

```bash
tag="$(cat "$HERE/build/bin/FFMPEG_RELEASE" 2>/dev/null)" || { echo "run fetch-ffmpeg.sh first" >&2; exit 1; }
release_url="https://github.com/mandakan/splitsmith/releases/tag/$tag"
```

(macOS: same tag, same URL as before.)

- [ ] **Step 6: Verify**

Run: `rm -rf desktop/build/bin && desktop/fetch-ffmpeg.sh && cat desktop/build/bin/FFMPEG_RELEASE && desktop/build/bin/ffmpeg -version | head -1`
Expected: `ffmpeg-linux-x86_64-9.0.2-r1`, `ffmpeg version 9.0.2`. Then corrupt the sha (one character) and confirm the fetch fails; revert.

- [ ] **Step 7: Frame check (ffmpeg change rule).** With the demo match (`uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match --media`), run `scripts/render_match_frames.py` and `scripts/render_grid_frames.py` once with `SPLITSMITH_FFMPEG`/`SPLITSMITH_FFPROBE` pointing at the Linux build and once at the macOS build's numbers on file (or the user's Mac). Publish the frames as an Artifact (gaspode is headless) and get the user's eyes on them. Differences at edges from x264 rate control are expected; text placement, colours and timing are not.

- [ ] **Step 8: Commit** `build(desktop): static Linux ffmpeg, fetched per target`

---

### Task 8: electron-builder Linux targets, deb scripts, icon, `build.sh` flags

**Files:**
- Modify: `desktop/electron-builder.yml`, `desktop/build.sh`, `desktop/package.json` (`pack` script only)
- Create: `desktop/linux/postinst.sh`, `desktop/linux/postrm.sh`, `desktop/linux/cli-link.sh`, `desktop/build-resources/linux-icons/512x512.png`, `desktop/src/cliLinkDeb.test.ts`

**Interfaces:**
- Consumes: Tasks 6-7 scripts.
- Produces: `build.sh [--mac|--linux] [--wheel PATH]`; artifacts `dist/Splitsmith-X.Y.Z-x86_64.AppImage`, `dist/splitsmith-desktop_X.Y.Z_amd64.deb`, `dist/linux-unpacked/`.

- [ ] **Step 1: The CLI link snippet, testable on its own.** `desktop/linux/cli-link.sh` holds the two functions; postinst/postrm call them. Because electron-builder templates the scripts, this file is *inlined* into both by `build.sh` (Step 4), not sourced at install time. Content (no `${letters}` macros):

```bash
# --- splitsmith: /usr/bin/splitsmith -> the bundled CLI ---------------
# Ours only when it is a link into /opt/Splitsmith. A file or a link
# somewhere else (another install) is left alone on install and removal.
SPLITSMITH_BIN_DIR="${SPLITSMITH_BIN_DIR:-/usr/bin}"
SPLITSMITH_OPT_DIR="${SPLITSMITH_OPT_DIR:-/opt/Splitsmith}"
splitsmith_cli_link() {
  local link="$SPLITSMITH_BIN_DIR/splitsmith" src="$SPLITSMITH_OPT_DIR/resources/python/bin/splitsmith"
  if [ -e "$link" ] || [ -L "$link" ]; then
    case "$(readlink "$link" 2>/dev/null)" in
      "$SPLITSMITH_OPT_DIR"/*) ;;
      *) echo "splitsmith: $link exists and is not ours; leaving it" >&2; return 0 ;;
    esac
  fi
  ln -sfn "$src" "$link"
}
splitsmith_cli_unlink() {
  local link="$SPLITSMITH_BIN_DIR/splitsmith"
  case "$(readlink "$link" 2>/dev/null)" in
    "$SPLITSMITH_OPT_DIR"/*) rm -f "$link" ;;
  esac
}
```

- [ ] **Step 2: Failing test** `cliLinkDeb.test.ts`:

```ts
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SNIPPET = path.join(__dirname, "..", "linux", "cli-link.sh");

function run(fn: string, bin: string, opt: string): void {
  const r = spawnSync("bash", ["-c", `source '${SNIPPET}' && ${fn}`], {
    env: { PATH: "/usr/bin:/bin", SPLITSMITH_BIN_DIR: bin, SPLITSMITH_OPT_DIR: opt },
    encoding: "utf8",
  });
  expect(r.status).toBe(0);
}

function dirs() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "deb-"));
  const bin = path.join(root, "bin");
  const opt = path.join(root, "opt", "Splitsmith");
  fs.mkdirSync(bin, { recursive: true });
  fs.mkdirSync(path.join(opt, "resources", "python", "bin"), { recursive: true });
  return { bin, opt, link: path.join(bin, "splitsmith"), src: path.join(opt, "resources", "python", "bin", "splitsmith") };
}

describe("deb CLI link", () => {
  it("links on install and again on reinstall", () => {
    const d = dirs();
    run("splitsmith_cli_link", d.bin, d.opt);
    run("splitsmith_cli_link", d.bin, d.opt);
    expect(fs.readlinkSync(d.link)).toBe(d.src);
  });
  it("leaves a foreign file and a foreign link alone, on install and removal", () => {
    const d = dirs();
    fs.writeFileSync(d.link, "#!/bin/sh\n");
    run("splitsmith_cli_link", d.bin, d.opt);
    run("splitsmith_cli_unlink", d.bin, d.opt);
    expect(fs.readFileSync(d.link, "utf8")).toBe("#!/bin/sh\n");
    fs.rmSync(d.link);
    fs.symlinkSync("/home/me/.local/bin/splitsmith", d.link);
    run("splitsmith_cli_link", d.bin, d.opt);
    run("splitsmith_cli_unlink", d.bin, d.opt);
    expect(fs.readlinkSync(d.link)).toBe("/home/me/.local/bin/splitsmith");
  });
  it("removes its own link", () => {
    const d = dirs();
    run("splitsmith_cli_link", d.bin, d.opt);
    run("splitsmith_cli_unlink", d.bin, d.opt);
    expect(fs.lstatSync(d.link, { throwIfNoEntry: false })).toBeUndefined();
  });
});
```

Run (before creating `cli-link.sh`): FAIL. Create it (Step 1), run: PASS. Then delete the `case ... esac` guard in `splitsmith_cli_link`, confirm "leaves a foreign file" FAILS, restore.

- [ ] **Step 3: The deb scripts.** Copy the stock templates verbatim from the locked electron-builder (`desktop/node_modules/.pnpm/node_modules/app-builder-lib/templates/linux/after-install.tpl` and `after-remove.tpl`) to `desktop/linux/postinst.sh` and `desktop/linux/postrm.sh`, with a first comment line: `# electron-builder 26.15.3 stock after-install.tpl, unchanged, plus the splitsmith block at the end (build.sh inlines linux/cli-link.sh at the marker).` Append to postinst:

```bash
# @@SPLITSMITH_CLI_LINK@@
splitsmith_cli_link
```

and to postrm:

```bash
# @@SPLITSMITH_CLI_LINK@@
splitsmith_cli_unlink
```

- [ ] **Step 4: `build.sh`.** Parse flags and route the target:

```bash
# shellcheck source=lib/target.sh
source "$HERE/lib/target.sh"
TARGET="$(host_target)"
WHEEL=""
while [ $# -gt 0 ]; do
  if t="$(parse_target_flag "$1")"; then TARGET="$t"; shift; continue; fi
  case "$1" in
    --wheel) WHEEL="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
```

- step 2 (wheel): skip `uv build` when `--wheel` was given.
- step 3: `"$HERE/build-runtime.sh" --target "$TARGET" ${WHEEL:+--wheel "$WHEEL"}`
- step 4: `"$HERE/fetch-ffmpeg.sh" --target "$TARGET"`
- step 6: on `linux-x86_64`, before electron-builder, render the deb scripts with the snippet inlined into `build/linux/`:

```bash
mkdir -p "$HERE/build/linux"
for f in postinst postrm; do
  awk -v snip="$HERE/linux/cli-link.sh" '/@@SPLITSMITH_CLI_LINK@@/ { while ((getline l < snip) > 0) print l; next } { print }' \
    "$HERE/linux/$f.sh" > "$HERE/build/linux/$f.sh"
done
```

  and the builder call is `pnpm exec electron-builder --linux --x64`; on `macos-aarch64` it stays `pnpm exec electron-builder --mac --arm64`. The signed-build verification and `ls -la "$HERE"/dist/*.dmg` run only on macOS; Linux lists `dist/*.AppImage dist/*.deb`.

`package.json` `pack` script: leave `--mac --arm64` (it is the macOS dev shortcut).

- [ ] **Step 5: The icon.** Render `site/favicon.svg` to 512x512 with the Playwright Chromium the repo already uses:

```bash
uv run python - <<'PY'
from pathlib import Path
from playwright.sync_api import sync_playwright
svg = Path("site/favicon.svg").read_text()
out = Path("desktop/build-resources/linux-icons/512x512.png"); out.parent.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"]); pg = b.new_page(viewport={"width": 512, "height": 512})
    pg.set_content(f'<html><body style="margin:0;background:transparent">{svg.replace("<svg ", "<svg width=\"512\" height=\"512\" ", 1)}</body></html>')
    pg.screenshot(path=str(out), omit_background=True); b.close()
PY
```

Look at the PNG (publish as an Artifact) before committing. It lives under `linux-icons/`, never `build-resources/icon.png`, which electron-builder would also pick up for macOS.

- [ ] **Step 6: `electron-builder.yml`.** Append (macOS blocks untouched):

```yaml
linux:
  target:
    - target: AppImage
      arch: [x64]
    - target: deb
      arch: [x64]
  category: AudioVideo
  executableName: splitsmith-desktop
  icon: build-resources/linux-icons
  synopsis: Shot splits from head-cam footage
  description: Splitsmith finds the beep and every shot in IPSC head-cam footage and turns them into splits, an audit trail and rendered videos.
  maintainer: Mathias Axell <m@thias.se>   # CONFIRM with the user before the first release
  extraResources:
    - from: build-resources/linux-icons/512x512.png
      to: icon.png
appImage:
  artifactName: ${productName}-${version}-x86_64.${ext}
  # The legacy toolset's desktop entry passes --no-sandbox unconditionally.
  executableArgs: []
deb:
  artifactName: splitsmith-desktop_${version}_amd64.${ext}
  depends: [libnss3, libgbm1, libasound2, libgtk-3-0, libxss1, libnotify4, xdg-utils, libatspi2.0-0, libdrm2, libxkbcommon0]
  afterInstall: build/linux/postinst.sh
  afterRemove: build/linux/postrm.sh
```

- [ ] **Step 7: Build on gaspode** (host build first; Task 10 does the container):

Run: `CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh --linux`
Expected: the AppImage and the .deb under `desktop/dist/`. Then check the deb kept the stock scripts and got ours:

```bash
cd ~/.claude-tmp && rm -rf debctl && dpkg-deb -e ~/work/splitsmith/desktop/dist/splitsmith-desktop_*_amd64.deb debctl
grep -c chrome-sandbox debctl/postinst; grep -c apparmor debctl/postinst; grep -c splitsmith_cli_link debctl/postinst; grep -c splitsmith_cli_unlink debctl/postrm
dpkg-deb -I ~/work/splitsmith/desktop/dist/splitsmith-desktop_*_amd64.deb | grep -E 'Depends|Maintainer|Description'
```

Expected: each count >= 1. Save these checks as `desktop/scripts/check-deb.sh <deb>` (exit 1 on any zero count) for Task 10.

- [ ] **Step 8: Run the vitest suite and commit**

```bash
cd desktop && corepack pnpm exec vitest run && cd ..
git add desktop/electron-builder.yml desktop/build.sh desktop/linux desktop/build-resources desktop/scripts/check-deb.sh desktop/src/cliLinkDeb.test.ts
git commit -m "build(desktop): AppImage and .deb targets; the deb links /usr/bin/splitsmith"
```

---

### Task 9: The wheel the release ships comes from PyPI

**Files:**
- Create: `scripts/ci/fetch_pypi_wheel.py`, `tests/test_fetch_pypi_wheel.py`

**Interfaces:**
- Produces: `pick_wheel(release_json: dict) -> tuple[str, str, str]` (filename, url, sha256); CLI `uv run --no-project python scripts/ci/fetch_pypi_wheel.py <version> <out_dir>` prints the downloaded path.

- [ ] **Step 1: Failing tests** `tests/test_fetch_pypi_wheel.py`:

```python
import hashlib
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "fetch_pypi_wheel", Path(__file__).parents[1] / "scripts" / "ci" / "fetch_pypi_wheel.py"
)
fpw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fpw)


def _release(*files: dict) -> dict:
    return {"urls": list(files)}


def _file(name: str, kind: str = "bdist_wheel", sha: str = "ab" * 32) -> dict:
    return {"filename": name, "packagetype": kind, "url": f"https://files/{name}", "digests": {"sha256": sha}}


def test_picks_the_pure_wheel_over_the_sdist() -> None:
    rel = _release(_file("splitsmith-1.2.3.tar.gz", "sdist"), _file("splitsmith-1.2.3-py3-none-any.whl"))
    assert fpw.pick_wheel(rel) == ("splitsmith-1.2.3-py3-none-any.whl", "https://files/splitsmith-1.2.3-py3-none-any.whl", "ab" * 32)


def test_no_pure_wheel_is_an_error() -> None:
    with pytest.raises(fpw.NoWheel):
        fpw.pick_wheel(_release(_file("splitsmith-1.2.3.tar.gz", "sdist")))


def test_fetch_retries_until_the_version_appears_then_verifies(tmp_path: Path) -> None:
    body = b"wheel-bytes"
    sha = hashlib.sha256(body).hexdigest()
    rel = _release(_file("splitsmith-1.2.3-py3-none-any.whl", sha=sha))
    answers = [None, None, rel]  # 404, 404, then the release
    out = fpw.fetch(
        "1.2.3", tmp_path, get_json=lambda url: answers.pop(0), get_bytes=lambda url: body,
        attempts=5, sleep=lambda s: None,
    )
    assert out == tmp_path / "splitsmith-1.2.3-py3-none-any.whl"
    assert out.read_bytes() == body


def test_fetch_gives_up_after_its_attempts(tmp_path: Path) -> None:
    with pytest.raises(fpw.NotYetPublished):
        fpw.fetch("1.2.3", tmp_path, get_json=lambda url: None, get_bytes=lambda url: b"", attempts=3, sleep=lambda s: None)


def test_fetch_refuses_a_sha_mismatch(tmp_path: Path) -> None:
    rel = _release(_file("splitsmith-1.2.3-py3-none-any.whl", sha="00" * 32))
    with pytest.raises(fpw.ShaMismatch):
        fpw.fetch("1.2.3", tmp_path, get_json=lambda url: rel, get_bytes=lambda url: b"x", attempts=1, sleep=lambda s: None)
    assert not (tmp_path / "splitsmith-1.2.3-py3-none-any.whl").exists()
```

- [ ] **Step 2:** `uv run pytest -n0 tests/test_fetch_pypi_wheel.py -q` -- FAIL, file missing.

- [ ] **Step 3: Implement** `scripts/ci/fetch_pypi_wheel.py` (stdlib only):

```python
"""Download the splitsmith wheel PyPI serves for one version.

The Linux desktop bundle ships the published wheel, never one built from a
checkout: publish-pypi.yml bakes the YouTube OAuth client into the wheel,
and a checkout has empty constants. PyPI's JSON can lag the upload by a
minute, so a missing version is retried before the job fails.

    uv run --no-project python scripts/ci/fetch_pypi_wheel.py 0.53.0 build/wheel
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

JSON_URL = "https://pypi.org/pypi/splitsmith/{version}/json"


class NoWheel(RuntimeError):
    pass


class NotYetPublished(RuntimeError):
    pass


class ShaMismatch(RuntimeError):
    pass


def pick_wheel(release: dict) -> tuple[str, str, str]:
    for f in release.get("urls", []):
        if f.get("packagetype") == "bdist_wheel" and f["filename"].endswith("-py3-none-any.whl"):
            return f["filename"], f["url"], f["digests"]["sha256"]
    raise NoWheel("PyPI lists no py3-none-any wheel for this version")


def _get_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _get_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def fetch(
    version: str,
    out_dir: Path,
    *,
    get_json: Callable[[str], dict | None] = _get_json,
    get_bytes: Callable[[str], bytes] = _get_bytes,
    attempts: int = 20,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    release = None
    for i in range(attempts):
        release = get_json(JSON_URL.format(version=version))
        if release is not None:
            break
        if i + 1 < attempts:
            sleep(15)
    if release is None:
        raise NotYetPublished(f"splitsmith {version} is not on PyPI after {attempts} attempts")
    name, url, sha = pick_wheel(release)
    body = get_bytes(url)
    got = hashlib.sha256(body).hexdigest()
    if got != sha:
        raise ShaMismatch(f"{name}: PyPI says {sha}, downloaded {got}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / name
    out.write_bytes(body)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: fetch_pypi_wheel.py <version> <out_dir>")
    print(fetch(sys.argv[1].removeprefix("v"), Path(sys.argv[2])))
```

- [ ] **Step 4:** `uv run pytest -n0 tests/test_fetch_pypi_wheel.py -q` -- PASS. Then a live check against the current release: `uv run --no-project python scripts/ci/fetch_pypi_wheel.py 0.52.0 ~/.claude-tmp/wheel` and `unzip -p ~/.claude-tmp/wheel/*.whl splitsmith/youtube/oauth.py | grep BUILTIN_CLIENT_ID` -- non-empty value.

- [ ] **Step 5: Commit** `ci: fetch the published wheel for the desktop bundle`

---

### Task 10: Smoke, launch check, and the CI workflows

**Files:**
- Modify: `desktop/smoke.sh`, `.github/workflows/desktop.yml`, `.github/workflows/release-please.yml`
- Create: `desktop/launch-check.sh`, `desktop/ci-linux-deps.sh`, `.github/workflows/desktop-linux.yml`

**Interfaces:**
- Consumes: `build.sh --linux --wheel`, `fetch_pypi_wheel.py`, `scripts/check-deb.sh`.
- Produces: reusable workflow `desktop-linux.yml` with input `release_tag` (string, optional; empty = PR build, no upload).

- [ ] **Step 1: `smoke.sh` per platform.** Replace the `APP=`/`RES=` lines:

```bash
APP="${1:-}"
if [ -z "$APP" ]; then
  case "$(uname -s)" in
    Darwin) APP="$HERE/dist/mac-arm64/Splitsmith.app" ;;
    *) APP="$HERE/dist/linux-unpacked" ;;
  esac
fi
if [ -d "$APP/Contents/Resources" ]; then RES="$APP/Contents/Resources"; else RES="$APP/resources"; fi
```

(define `HERE="$(cd "$(dirname "$0")" && pwd)"` above it). The encoder check becomes:

```bash
encoders="$("$RES/bin/ffmpeg" -hide_banner -encoders)"
filters="$("$RES/bin/ffmpeg" -hide_banner -filters)"
if [ "$(uname -s)" = Darwin ]; then
  grep -q h264_videotoolbox <<<"$encoders" || { echo "bundled ffmpeg lacks h264_videotoolbox"; exit 1; }
else
  grep -q libx264 <<<"$encoders" || { echo "bundled ffmpeg lacks libx264"; exit 1; }
  grep -q drawtext <<<"$filters" || { echo "bundled ffmpeg lacks drawtext"; exit 1; }
fi
```

Run on gaspode: `desktop/smoke.sh` -- `smoke ok: .../dist/linux-unpacked`. Then point `SPLITSMITH_FFMPEG` resolution wrong (rename `resources/bin/ffmpeg` temporarily) and confirm it fails; restore.

- [ ] **Step 2: `desktop/launch-check.sh`** -- starts the packed app from the AppImage's own contents:

```bash
#!/usr/bin/env bash
# Start the built AppImage's own Electron + main.js + sidecar under xvfb and
# wait for the sidecar to answer, then shut it down. Runs the extracted
# binary with --no-sandbox and APPIMAGE unset on purpose: CI has no FUSE,
# Docker's seccomp blocks unshare, and the ubuntu-latest host restricts
# user namespaces -- the sandbox refusal would (correctly) trigger in all
# three. sandbox.test.ts and the manual checklist cover the refusal.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="$(ls "$HERE"/dist/*.AppImage | head -1)"
WORK="$(mktemp -d)"
trap 'kill "$pid" 2>/dev/null || true; rm -rf "$WORK"' EXIT
(cd "$WORK" && "$IMG" --appimage-extract >/dev/null)
export HOME="$WORK/home" XDG_STATE_HOME="$WORK/state" XDG_CACHE_HOME="$WORK/cache" SPLITSMITH_AUTO_SYNC=0
mkdir -p "$HOME"
unset APPIMAGE
xvfb-run -a "$WORK/squashfs-root/splitsmith-desktop" --no-sandbox >"$WORK/electron.log" 2>&1 &
pid=$!
log="$XDG_STATE_HOME/splitsmith/logs"
for _ in $(seq 1 120); do
  base="$(grep -rhoE 'http://127\.0\.0\.1:[0-9]+' "$log" 2>/dev/null | head -1 || true)"
  [ -n "$base" ] && curl -fsS "$base/api/health" >/dev/null 2>&1 && break
  sleep 1
done
[ -n "${base:-}" ] || { echo "no sidecar URL in $log"; cat "$WORK/electron.log"; exit 1; }
curl -fsS "$base/api/health" | grep -q '"status":"ok"'
echo "launch ok: $IMG ($base)"
```

Note: `SPLITSMITH_AUTO_SYNC=0` is scrubbed by `sidecarSpec` (it drops `SPLITSMITH_*`); the fresh `HOME` is what keeps it away from any real token. Confirm on the first run where the sidecar writes its base URL (`ui/embedded.py` `--log-dir`); if the log does not carry it, read it from `electron.log` instead and adjust the grep. Run it on gaspode: `launch ok`.

- [ ] **Step 3: `desktop/ci-linux-deps.sh`** -- one place for the container's packages:

```bash
#!/usr/bin/env bash
# Packages the ubuntu:22.04 build container needs for desktop/build.sh --linux,
# smoke.sh and launch-check.sh. Node/pnpm/uv come from their setup actions.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates xz-utils file dpkg-dev \
  xvfb xauth libnss3 libgbm1 libasound2 libgtk-3-0 libxss1 libnotify4 libatspi2.0-0 libdrm2 libxkbcommon0 \
  >/dev/null
```

- [ ] **Step 4: `.github/workflows/desktop-linux.yml`:**

```yaml
name: desktop-linux

# The x86_64 AppImage and .deb (spec 2026-10-02). Called by release-please.yml
# after PyPI has the version (the bundle ships that wheel), and by desktop.yml
# on PRs with no tag (checkout-built wheel, artifacts only, no upload).
on:
  workflow_call:
    inputs:
      release_tag:
        type: string
        default: ""
  workflow_dispatch:
    inputs:
      release_tag:
        description: "vX.Y.Z to build and attach (re-run after a failure)"
        required: true

permissions:
  contents: read

jobs:
  build:
    name: linux x86_64 AppImage + deb
    runs-on: ubuntu-latest
    container: ubuntu:22.04
    timeout-minutes: 60
    permissions:
      contents: write
    steps:
      - name: Container packages
        run: |
          apt-get update -qq && apt-get install -y -qq git ca-certificates curl >/dev/null
      - uses: actions/checkout@v5
        with:
          ref: ${{ inputs.release_tag || github.ref }}
      - run: desktop/ci-linux-deps.sh
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - uses: pnpm/action-setup@v4
      - uses: actions/cache@v4
        with:
          path: ~/.splitsmith/models
          key: models-${{ hashFiles('src/splitsmith/data/ensemble_calibration.json') }}

      - name: Published wheel
        if: inputs.release_tag != ''
        run: |
          wheel="$(uv run --no-project python scripts/ci/fetch_pypi_wheel.py "${{ inputs.release_tag }}" build/wheel)"
          echo "WHEEL=$PWD/$wheel" >> "$GITHUB_ENV"

      - name: Build
        run: desktop/build.sh --linux ${WHEEL:+--wheel "$WHEEL"}

      - name: The deb kept the stock scripts
        run: desktop/scripts/check-deb.sh desktop/dist/*.deb

      - name: Smoke
        run: desktop/smoke.sh
      - name: Launch
        run: desktop/launch-check.sh

      - name: Checksums
        run: cd desktop/dist && sha256sum *.AppImage *.deb > SHA256SUMS && cat SHA256SUMS

      - uses: actions/upload-artifact@v4
        with:
          name: Splitsmith-linux-x86_64
          path: |
            desktop/dist/*.AppImage
            desktop/dist/*.deb
            desktop/dist/SHA256SUMS
          retention-days: 14

      - name: Attach to the release
        if: inputs.release_tag != ''
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          apt-get install -y -qq gh >/dev/null 2>&1 || (curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/gh.gpg \
            && echo "deb [signed-by=/usr/share/keyrings/gh.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/gh.list \
            && apt-get update -qq && apt-get install -y -qq gh >/dev/null)
          git config --global --add safe.directory "$GITHUB_WORKSPACE"
          gh release upload "${{ inputs.release_tag }}" desktop/dist/*.AppImage desktop/dist/*.deb desktop/dist/SHA256SUMS --clobber
```

Note: `desktop/build.sh` runs `git checkout -q -- package.json` in its trap; inside the container that needs the `safe.directory` line too -- move that `git config` line to a step right after checkout.

- [ ] **Step 5: Wire it.** In `release-please.yml`, after `publish-image`:

```yaml
  desktop-linux:
    # The Linux AppImage + deb, from the wheel PyPI now serves. The DMG
    # stays a local step (desktop/release.sh); the feed announces each
    # platform only once its own assets are attached.
    needs: [release-please, publish-pypi]
    if: needs.release-please.outputs.release_created == 'true'
    uses: ./.github/workflows/desktop-linux.yml
    with:
      release_tag: ${{ needs.release-please.outputs.tag_name }}
    permissions:
      contents: write
```

In `desktop.yml`, add `".github/workflows/desktop-linux.yml"` and `"scripts/ci/fetch_pypi_wheel.py"` to the PR path filter and a job:

```yaml
  linux:
    uses: ./.github/workflows/desktop-linux.yml
    permissions:
      contents: write
```

(no `release_tag`: build, smoke, launch, artifact; no upload. `contents: write` is required because the called job declares it; nothing writes without a tag.) The macOS job and its triggers stay as they are.

- [ ] **Step 6: Run it.** Push the branch, open a draft PR, and watch `gh pr checks <n> --watch`. Expected: `desktop / linux` green, artifacts downloadable. Download them on gaspode and rerun `desktop/scripts/check-deb.sh` on the CI deb. A red container step is debugged in the container locally (`docker run -it -v $PWD:/w -w /w ubuntu:22.04`), not by pushing blind.

- [ ] **Step 7: Commit** (each file group as it goes green): `ci(desktop): build, smoke and attach the Linux AppImage and deb`

---

### Task 11: Manual verification, docs, and the release gate

**Files:**
- Modify: `CLAUDE.md` (the "Desktop app" section), `desktop/README` if present (none today -- skip)
- Modify: `docs/superpowers/specs/2026-10-02-desktop-linux-packaging-design.md` (status line)

- [ ] **Step 1: Manual checklist** -- run against the PR's CI artifacts, record results as a PR comment. Use a scratch `HOME` or `SPLITSMITH_AUTO_SYNC=0`; never a real hosted token.
  - AppImage on Arch (container with a desktop is not enough -- a real or VM session): opens, a seeded `--media` demo match detects, an export renders.
  - AppImage on Debian 12: same.
  - .deb on Debian 12: same; `splitsmith --version` in a terminal; a detection through `/usr/bin/splitsmith` shows up in the app (shared `~/.config/splitsmith`).
  - .deb on Ubuntu 24.04: opens; `ps -ef | grep splitsmith-desktop` shows no `--no-sandbox`.
  - AppImage on Ubuntu 24.04: the refusal dialog, the button opens the releases page, the process exits.
  - uvx install on the Debian box before and after the .deb: same matches.
  - `apt remove splitsmith-desktop`: `/usr/bin/splitsmith` gone, `~/.config/splitsmith` intact.
  - Frames from Task 7 Step 7 approved.

- [ ] **Step 2: CLAUDE.md.** In "Desktop app", after the macOS release sentence, add one paragraph: Linux ships as an x86_64 AppImage and .deb built by `.github/workflows/desktop-linux.yml` in `ubuntu:22.04` from the wheel PyPI serves (`scripts/ci/fetch_pypi_wheel.py`), chained after `publish-pypi` in `release-please.yml`; `desktop/build.sh --linux` builds locally; `lib/target.sh` is the one place a target is resolved; the deb's `linux/postinst.sh` / `postrm.sh` are electron-builder's stock scripts plus `linux/cli-link.sh` and must stay so (`scripts/check-deb.sh`); the AppImage refuses with a dialog where the sandbox cannot start (`src/sandbox.ts`); the feed's `?platform=linux` waits for both assets. Keep it to the facts a future change would trip on.

- [ ] **Step 3: Spec status** -> `Status: implemented (PR #<n>).`

- [ ] **Step 4: Commit, then hand back.** Merging and the first release are the user's call; flag the open `maintainer` line and the unverified macOS YouTube-client finding in the PR body.

---

## Self-review notes

- Spec coverage: artifacts (8, 10), runtime (6), ffmpeg (7), electron-builder (8), sidecar (3), menu (4), main/icon (5), updateCheck (2), sandbox (5, 8 executableArgs), feed (1), PyPI wheel (9), release job (10), smoke/launch (10), manual list (11). The macOS YouTube-client finding stays out of scope, as the spec says.
- Names used across tasks: `host_target`, `parse_target_flag`, `sha256_verify` (6 -> 7, 8); `platformDirs` (3); `menuTemplate(platform, handlers, appName)` (4); `sandboxBlocked`, `RELEASES_PAGE` (5); `pick_wheel`, `fetch` (9 -> 10); `check-deb.sh` (8 -> 10); `FFMPEG_RELEASE` (7).
