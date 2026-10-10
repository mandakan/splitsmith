import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Menu } from "./Menu";

function rect(top: number, height: number): DOMRect {
  return { top, bottom: top + height, left: 600, right: 640, width: 40, height, x: 600, y: top, toJSON: () => ({}) } as DOMRect;
}

// The anchor (the trigger's wrapper) sits at `anchorTop`; the popover is
// `menuHeight` tall. jsdom lays nothing out, so both rects are stubbed.
function renderAt(anchorTop: number, menuHeight: number) {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    return this.getAttribute("role") === "menu" ? rect(0, menuHeight) : rect(anchorTop, 30);
  });
  render(
    <span data-testid="anchor">
      <Menu open onClose={vi.fn()} align="right">
        <button type="button">Item</button>
      </Menu>
    </span>,
  );
  return screen.getByRole("menu");
}

describe("Menu placement", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens under its trigger when there is room below", () => {
    window.innerHeight = 900;
    const menu = renderAt(100, 300);
    expect(menu.style.top).toBe("134px");
  });

  it("flips above its trigger when the room below cannot hold it", () => {
    // Audit's band header near the bottom of a 900 px screen.
    window.innerHeight = 900;
    const menu = renderAt(780, 300);
    expect(menu.style.top).toBe(`${780 - 4 - 300}px`);
  });
});
