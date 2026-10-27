import { describe, expect, it } from "vitest";

import { cliRelative, isSidecarOrigin, parseReadyLine, platformDirs, pythonRelative, sidecarSpec } from "./sidecar";

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
    env: { PATH: "/usr/bin", PYTHONPATH: "/evil", PYTHONHOME: "/evil", HOME: "/Users/me", SPLITSMITH_PROJECT_ROOT: "/x" },
    platform: "darwin",
  });
  it("runs the bundled interpreter as the embedded module", () => {
    expect(spec.command).toBe("/App.app/Contents/Resources/python/bin/python3.12");
    expect(spec.args).toEqual(["-m", "splitsmith.ui.embedded", "--log-dir", "/Users/me/Library/Logs/Splitsmith"]);
    expect(spec.logDir).toBe("/Users/me/Library/Logs/Splitsmith");
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
  it("drops inherited Python and splitsmith variables and never sets a project root", () => {
    expect(spec.env).not.toHaveProperty("PYTHONPATH");
    expect(spec.env).not.toHaveProperty("PYTHONHOME");
    expect(spec.env).not.toHaveProperty("SPLITSMITH_PROJECT_ROOT");
    expect(spec.env.PATH).toBe("/usr/bin");
    expect(spec.env.HOME).toBe("/Users/me");
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

describe("sidecarSpec on Windows", () => {
  const base = {
    resourcesPath: "C:\\Users\\me\\AppData\\Local\\Programs\\Splitsmith\\resources",
    port: 5000,
    home: "C:\\Users\\me",
  };
  it("runs python.exe from the runtime root and the .exe binaries", () => {
    const spec = sidecarSpec({ ...base, platform: "win32", env: { LOCALAPPDATA: "C:\\Users\\me\\AppData\\Local" } });
    expect(spec.command).toBe("C:\\Users\\me\\AppData\\Local\\Programs\\Splitsmith\\resources\\python\\python.exe");
    expect(spec.env.SPLITSMITH_FFMPEG).toBe("C:\\Users\\me\\AppData\\Local\\Programs\\Splitsmith\\resources\\bin\\ffmpeg.exe");
    expect(spec.env.SPLITSMITH_FFPROBE).toBe("C:\\Users\\me\\AppData\\Local\\Programs\\Splitsmith\\resources\\bin\\ffprobe.exe");
  });
  it("logs and caches under %LOCALAPPDATA%, next to the engine's cache", () => {
    const spec = sidecarSpec({ ...base, platform: "win32", env: { LOCALAPPDATA: "D:\\Local" } });
    expect(spec.logDir).toBe("D:\\Local\\splitsmith\\logs");
    expect(spec.args).toEqual(["-m", "splitsmith.ui.embedded", "--log-dir", "D:\\Local\\splitsmith\\logs"]);
    expect(spec.env.NUMBA_CACHE_DIR).toBe("D:\\Local\\splitsmith\\numba");
  });
  it("falls back to AppData\\Local when LOCALAPPDATA is missing or relative", () => {
    for (const env of [{}, { LOCALAPPDATA: "" }, { LOCALAPPDATA: "Local" }]) {
      expect(platformDirs("win32", "C:\\Users\\me", env)).toEqual({
        logDir: "C:\\Users\\me\\AppData\\Local\\splitsmith\\logs",
        numbaCacheDir: "C:\\Users\\me\\AppData\\Local\\splitsmith\\numba",
      });
    }
  });
  it("drops Python and splitsmith variables whatever their case", () => {
    const spec = sidecarSpec({
      ...base,
      platform: "win32",
      env: { Path: "C:\\Windows", PythonPath: "C:\\evil", Splitsmith_Project_Root: "C:\\x" },
    });
    expect(spec.env).not.toHaveProperty("PythonPath");
    expect(spec.env).not.toHaveProperty("Splitsmith_Project_Root");
    expect(spec.env.Path).toBe("C:\\Windows");
  });
});

describe("runtime layout", () => {
  it("names the interpreter and console script per platform", () => {
    expect(pythonRelative("darwin")).toBe("python/bin/python3.12");
    expect(pythonRelative("linux")).toBe("python/bin/python3.12");
    expect(pythonRelative("win32")).toBe("python\\python.exe");
    expect(cliRelative("darwin")).toBe("python/bin/splitsmith");
    expect(cliRelative("win32")).toBe("python\\Scripts\\splitsmith.exe");
  });
});
