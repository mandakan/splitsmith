/**
 * The only bridge into the renderer, and only the loading page uses it:
 * the app version for the "Starting engine" line, the failure feed, and
 * two buttons. The SPA never calls window.splitsmith.
 */
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("splitsmith", {
  appVersion: process.env.SPLITSMITH_APP_VERSION ?? "",
  onFailure: (cb: (lines: string[]) => void) => {
    ipcRenderer.on("sidecar-failed", (_event, lines: string[]) => cb(lines));
  },
  openLogFolder: () => ipcRenderer.send("open-log-folder"),
  quit: () => ipcRenderer.send("quit"),
});
