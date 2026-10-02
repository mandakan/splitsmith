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
