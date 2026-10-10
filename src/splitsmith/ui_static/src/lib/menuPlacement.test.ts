import { describe, expect, it } from "vitest";

import { MENU_GAP, menuTop } from "./menuPlacement";

describe("menuTop", () => {
  it("opens under the trigger when the menu fits below", () => {
    expect(menuTop({ top: 100, bottom: 130 }, 200, 900)).toBe(130 + MENU_GAP);
  });

  it("flips above the trigger when there is no room below", () => {
    // A trigger 70 px from the bottom of a 900 px screen and a 300 px menu.
    expect(menuTop({ top: 800, bottom: 830 }, 300, 900)).toBe(800 - MENU_GAP - 300);
  });

  it("stays below when it fits neither way but below has more room", () => {
    expect(menuTop({ top: 200, bottom: 230 }, 800, 900)).toBe(230 + MENU_GAP);
  });

  it("never places the flipped menu above the top of the screen", () => {
    expect(menuTop({ top: 500, bottom: 530 }, 700, 600)).toBe(0);
  });
});
