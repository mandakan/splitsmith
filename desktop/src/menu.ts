/**
 * The application menu: About (both versions), third-party notices, the
 * command-line tool installer and the log folder, plus the standard roles.
 */
import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { app, BrowserWindow, dialog, Menu, shell } from "electron";

import { CLI_TARGET, cliLinkPlan } from "./cliLink";
import { CLI_RELATIVE, sidecarState } from "./sidecar";

function resourcesPath(): string {
  return process.env.SPLITSMITH_RESOURCES ?? process.resourcesPath;
}

/**
 * The window a dialog attaches to. Without a parent Electron runs the
 * alert as a blocking modal on macOS, and with no window open that modal
 * is invisible and wedges the main process (seen on the first signed
 * build), so a dialog with no window to attach to is not shown at all.
 */
function parentWindow(): BrowserWindow | null {
  return BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0] ?? null;
}

function showAbout(): void {
  const parent = parentWindow();
  if (!parent) return;
  void dialog
    .showMessageBox(parent, {
      type: "info",
      title: "About Splitsmith",
      message: "Splitsmith",
      detail: `App ${app.getVersion()}\nEngine ${sidecarState.engineVersion || "not started"}`,
      buttons: ["Third-party notices", "OK"],
      defaultId: 1,
      cancelId: 1,
    })
    .then(({ response }) => {
      if (response === 0) showNotices();
    });
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c] ?? c);
}

function showNotices(): void {
  const file = path.join(resourcesPath(), "NOTICES.md");
  const text = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "NOTICES.md is missing from this build.";
  const w = new BrowserWindow({
    width: 720,
    height: 800,
    title: "Third-party notices",
    backgroundColor: "#0e0f11",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  const html =
    "<!doctype html><meta charset=\"utf-8\"><style>body{background:#0e0f11;color:#e6e6e6;" +
    "font:13px/1.5 -apple-system,sans-serif;padding:24px}pre{white-space:pre-wrap}</style>" +
    `<pre>${escapeHtml(text)}</pre>`;
  void w.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
}

/** What ``target`` resolves to today: a symlink's value, a file's own path, or null. */
function currentTarget(target: string): string | null {
  try {
    const st = fs.lstatSync(target);
    return st.isSymbolicLink() ? fs.readlinkSync(target) : target;
  } catch {
    return null;
  }
}

function report(message: string, detail: string, type: "info" | "warning" | "error" = "info"): void {
  const parent = parentWindow();
  if (!parent) return;
  void dialog.showMessageBox(parent, { type, message, detail });
}

/** Symlink the bundle's console script to /usr/local/bin, with an admin prompt when needed. */
function installCli(): void {
  const source = path.join(resourcesPath(), CLI_RELATIVE);
  const plan = cliLinkPlan(source, CLI_TARGET, currentTarget(CLI_TARGET));
  if (plan.kind === "already") {
    report("Already installed", `${CLI_TARGET} points at this app.`);
    return;
  }
  if (plan.kind === "conflict") {
    report("Not installed", `${CLI_TARGET} is ${plan.existing}, which is not this app. Remove it first.`, "warning");
    return;
  }
  const installed = () => report("Installed", "Run 'splitsmith' in a terminal.");
  try {
    fs.mkdirSync(path.dirname(plan.target), { recursive: true });
    try {
      fs.unlinkSync(plan.target);
    } catch {
      // nothing there
    }
    fs.symlinkSync(plan.source, plan.target);
    installed();
  } catch {
    const cmd = `mkdir -p '${path.dirname(plan.target)}' && ln -sfn '${plan.source}' '${plan.target}'`;
    const script = `do shell script "${cmd.replace(/"/g, '\\"')}" with administrator privileges`;
    execFile("osascript", ["-e", script], (err) => {
      if (!err) installed();
      // A cancelled password prompt is not an error worth a dialog.
      else if (!/User canceled|-128/.test(err.message)) report("Not installed", err.message, "error");
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
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    {
      label: "File",
      submenu: [{ label: "Open log folder", click: () => void shell.openPath(sidecarState.logDir) }, { role: "close" }],
    },
    { role: "editMenu" },
    {
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
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
