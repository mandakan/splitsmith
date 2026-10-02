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
