/**
 * Pure helpers for launching the engine sidecar. No Electron imports, so
 * they run under vitest; main.ts wires them to the process.
 */
import path from "node:path";

export const READY_PREFIX = "SPLITSMITH_READY ";

/**
 * ``path`` for the platform being described, not the host: the helpers take
 * ``platform`` as an argument, and the tests ask for win32 paths on a Mac.
 */
function pathFor(platform: NodeJS.Platform): path.PlatformPath {
  return platform === "win32" ? path.win32 : path.posix;
}

/**
 * The bundled interpreter, relative to the resources dir. python-build-standalone
 * puts ``python.exe`` at the root on Windows and ``bin/python3.12`` elsewhere.
 */
export function pythonRelative(platform: NodeJS.Platform): string {
  return platform === "win32" ? path.win32.join("python", "python.exe") : path.posix.join("python", "bin", "python3.12");
}

/** The runtime's ``splitsmith`` console script (``Scripts\splitsmith.exe`` on Windows). */
export function cliRelative(platform: NodeJS.Platform): string {
  return platform === "win32"
    ? path.win32.join("python", "Scripts", "splitsmith.exe")
    : path.posix.join("python", "bin", "splitsmith");
}

/** The JSON the sidecar prints after ``SPLITSMITH_READY`` (ui/embedded.py ServerHandle). */
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
  /** ``Contents/Resources`` of the app bundle (or a dev stand-in). */
  resourcesPath: string;
  port: number;
  home: string;
  env: NodeJS.ProcessEnv;
  /** ``process.platform``; passed in so this stays pure. */
  platform: NodeJS.Platform;
}

/**
 * An env var naming a base dir, used only when it is absolute (XDG requires
 * it, user_config.py honours it the same way); otherwise ``fallback``.
 */
function absoluteEnv(env: NodeJS.ProcessEnv, name: string, fallback: string, p: path.PlatformPath): string {
  const v = env[name];
  return v && p.isAbsolute(v) ? v : fallback;
}

/**
 * Where the sidecar logs and where numba caches. macOS keeps the paths
 * it shipped with; Linux follows XDG and Windows uses %LOCALAPPDATA%, both
 * next to the engine's own cache dir (runtime._platform_cache_dir). Data
 * (SPLITSMITH_HOME) is never set:
 * the engine picks the same place the CLI does.
 */
export function platformDirs(
  platform: NodeJS.Platform,
  home: string,
  env: NodeJS.ProcessEnv,
): { logDir: string; numbaCacheDir: string } {
  const p = pathFor(platform);
  if (platform === "win32") {
    const local = absoluteEnv(env, "LOCALAPPDATA", p.join(home, "AppData", "Local"), p);
    return { logDir: p.join(local, "splitsmith", "logs"), numbaCacheDir: p.join(local, "splitsmith", "numba") };
  }
  if (platform === "linux") {
    const state = absoluteEnv(env, "XDG_STATE_HOME", p.join(home, ".local", "state"), p);
    const cache = absoluteEnv(env, "XDG_CACHE_HOME", p.join(home, ".cache"), p);
    return { logDir: p.join(state, "splitsmith", "logs"), numbaCacheDir: p.join(cache, "splitsmith", "numba") };
  }
  return {
    logDir: p.join(home, "Library", "Logs", "Splitsmith"),
    numbaCacheDir: p.join(home, "Library", "Caches", "Splitsmith", "numba"),
  };
}

export interface SidecarSpec {
  command: string;
  args: string[];
  env: Record<string, string>;
  logDir: string;
}

/**
 * The spawn recipe. The user's shell environment is passed through minus
 * every PYTHON* and SPLITSMITH_* variable: a stray PYTHONPATH would put a
 * different splitsmith in front of the bundled one, and a leftover
 * SPLITSMITH_PROJECT_ROOT would skip the picker. The engine's own env
 * vars are then set explicitly (see splitsmith.runtime for the list).
 */
export function sidecarSpec({ resourcesPath, port, home, env, platform }: SidecarOptions): SidecarSpec {
  const p = pathFor(platform);
  const exe = platform === "win32" ? ".exe" : "";
  const { logDir, numbaCacheDir } = platformDirs(platform, home, env);
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined) continue;
    // Windows env names are case-insensitive: ``PythonPath`` is PYTHONPATH there.
    const name = platform === "win32" ? k.toUpperCase() : k;
    if (name.startsWith("PYTHON") || name.startsWith("SPLITSMITH_")) continue;
    clean[k] = v;
  }
  return {
    command: p.join(resourcesPath, pythonRelative(platform)),
    args: ["-m", "splitsmith.ui.embedded", "--log-dir", logDir],
    env: {
      ...clean,
      SPLITSMITH_HOST: "127.0.0.1",
      SPLITSMITH_PORT: String(port),
      SPLITSMITH_FFMPEG: p.join(resourcesPath, "bin", `ffmpeg${exe}`),
      SPLITSMITH_FFPROBE: p.join(resourcesPath, "bin", `ffprobe${exe}`),
      // The bundle is read-only (signed on macOS, squashfs in an AppImage)
      // and an installer owns it on Windows; numba's default cache is next
      // to the module and would fail to write or be left behind on uninstall.
      NUMBA_CACHE_DIR: numbaCacheDir,
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONNOUSERSITE: "1",
    },
    logDir,
  };
}

/** True when ``url`` is on the sidecar's origin; anything else opens in the browser. */
export function isSidecarOrigin(url: string, baseUrl: string): boolean {
  try {
    return new URL(url).origin === new URL(baseUrl).origin;
  } catch {
    return false;
  }
}

/** What main.ts learns from the running sidecar; read by the menu at click time. */
export const sidecarState: { ready: ReadyPayload | null; engineVersion: string; logDir: string } = {
  ready: null,
  engineVersion: "",
  logDir: "",
};
