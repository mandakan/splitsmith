/**
 * Update check, Electron side: fetch the feed, remember a dismissed
 * version under userData, and put the answer on the main window as a
 * sheet. The decision logic is updateCheck.ts (tested).
 */
import fs from "node:fs";
import path from "node:path";

import { app, BrowserWindow, dialog, shell } from "electron";

import { FEED_TIMEOUT_MS, UPDATE_FEED_URL, parseFeed, updateDecision, type UpdateFeed } from "./updateCheck";

function feedUrl(): string {
  // Dev override so the sheet can be exercised against a local feed.
  return process.env.SPLITSMITH_UPDATE_FEED ?? UPDATE_FEED_URL;
}

function dismissedPath(): string {
  return path.join(app.getPath("userData"), "update-dismissed.json");
}

function readDismissed(): string | null {
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(dismissedPath(), "utf8"));
    const v = (parsed as { version?: unknown })?.version;
    return typeof v === "string" ? v : null;
  } catch {
    return null;
  }
}

function writeDismissed(version: string): void {
  try {
    fs.mkdirSync(path.dirname(dismissedPath()), { recursive: true });
    fs.writeFileSync(dismissedPath(), JSON.stringify({ version }));
  } catch {
    // A failed write only means the sheet shows again next launch.
  }
}

async function fetchFeed(): Promise<UpdateFeed | null> {
  try {
    const res = await fetch(feedUrl(), {
      signal: AbortSignal.timeout(FEED_TIMEOUT_MS),
      headers: { "user-agent": `splitsmith-desktop/${app.getVersion()}` },
    });
    if (!res.ok) return null;
    return parseFeed(await res.json());
  } catch {
    return null;
  }
}

function parentWindow(): BrowserWindow | null {
  return BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0] ?? null;
}

/**
 * ``interactive`` is the menu item: it always answers. The launch check
 * speaks only when a version newer than the dismissed one is available.
 */
export async function checkForUpdates(opts: { interactive: boolean }): Promise<void> {
  const feed = await fetchFeed();
  const decision = updateDecision({ current: app.getVersion(), feed, dismissed: readDismissed() });
  const parent = parentWindow();
  if (!parent) return;
  if (decision.kind === "available" || (decision.kind === "dismissed" && opts.interactive)) {
    const { response } = await dialog.showMessageBox(parent, {
      type: "info",
      message: `Splitsmith ${decision.version} is available`,
      detail: `You have ${app.getVersion()}.`,
      buttons: ["Download", "Later"],
      defaultId: 0,
      cancelId: 1,
    });
    if (response === 0) void shell.openExternal(decision.url);
    else writeDismissed(decision.version);
    return;
  }
  if (!opts.interactive) return;
  if (decision.kind === "current") {
    void dialog.showMessageBox(parent, { type: "info", message: "Splitsmith is up to date", detail: `Version ${app.getVersion()}.` });
  } else {
    void dialog.showMessageBox(parent, {
      type: "warning",
      message: "Could not check for updates",
      detail: "The update feed did not answer. Try again later.",
    });
  }
}
