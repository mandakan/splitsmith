/**
 * Electron main process: spawn the engine sidecar from the bundled Python
 * runtime, wait for its READY banner, show the SPA it serves. Everything
 * decidable without Electron lives in sidecar.ts and cliLink.ts (tested);
 * this file is wiring.
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";

import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";

import { buildMenu } from "./menu";
import { BLOCKED_DETAIL, BLOCKED_MESSAGE, RELEASES_PAGE, sandboxBlocked } from "./sandbox";
import { isSidecarOrigin, parseReadyLine, sidecarSpec, sidecarState } from "./sidecar";
import { checkForUpdates } from "./updates";

const READY_TIMEOUT_MS = 30_000;
const SHUTDOWN_GRACE_MS = 10_000;
const KILL_GRACE_MS = 5_000;
const TAIL_LINES = 50;
const UPDATE_CHECK_DELAY_MS = 5_000;
const LOADING_PAGE = path.join(__dirname, "..", "src", "loading.html");

/** ``Contents/Resources`` of the bundle; ``pnpm start`` points it at desktop/build/resources. */
export function resourcesPath(): string {
  return process.env.SPLITSMITH_RESOURCES ?? process.resourcesPath;
}

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

/** The whole error UI: the loading page in its failed state with the log tail. */
export function showFailure(lines: string[]): void {
  if (!win) return;
  void win.loadFile(LOADING_PAGE).then(() => win?.webContents.send("sidecar-failed", lines));
}

async function startSidecar(): Promise<void> {
  const port = await freePort();
  const spec = sidecarSpec({
    resourcesPath: resourcesPath(),
    port,
    home: os.homedir(),
    env: process.env,
    platform: process.platform,
  });
  sidecarState.logDir = spec.logDir;
  child = spawn(spec.command, spec.args, { env: spec.env, stdio: ["ignore", "ignore", "pipe"] });
  child.on("error", (err) => showFailure([`could not start ${spec.command}: ${err.message}`]));
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
        .then((h: { version?: string }) => {
          sidecarState.engineVersion = h.version ?? "";
        })
        .catch(() => undefined)
        .finally(() => void win?.loadURL(payload.base_url));
      // Never on the startup path: the SPA is up first, the feed is asked later.
      setTimeout(() => void checkForUpdates({ interactive: false }), UPDATE_CHECK_DELAY_MS);
    }
  });
  child.on("exit", (code, signal) => {
    clearTimeout(timer);
    child = null;
    if (quitting) return;
    showFailure([`engine exited (code ${code ?? "null"}, signal ${signal ?? "none"})`, ...tail]);
  });
}

/** POST /api/shutdown, then SIGTERM, then SIGKILL, each on its own grace period. */
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

function createWindow(): void {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    title: "Splitsmith",
    backgroundColor: "#0e0f11",
    icon: windowIcon(),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  // Anything off the sidecar's origin (share links, YouTube consent, the
  // marketing site) belongs in the default browser, not in this window.
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
  win.on("closed", () => {
    win = null;
  });
  void win.loadFile(LOADING_PAGE);
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
