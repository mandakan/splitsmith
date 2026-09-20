/**
 * Pure helpers for launching the engine sidecar. No Electron imports, so
 * they run under vitest; main.ts wires them to the process.
 */
import path from "node:path";

export const READY_PREFIX = "SPLITSMITH_READY ";
export const PYTHON_RELATIVE = path.join("python", "bin", "python3.12");
export const CLI_RELATIVE = path.join("python", "bin", "splitsmith");

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
      // The bundle is sealed by its signature; numba's default cache is next
      // to the module and would fail to write.
      NUMBA_CACHE_DIR: path.join(home, "Library", "Caches", "Splitsmith", "numba"),
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
