import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Menu } from "./Menu";
import { Sheet } from "./Sheet";

// An Escape a menu or sheet closes on is consumed (preventDefault), so a
// page-level Escape (Breakdown drops its region) leaves that press alone.
describe("Menu and Sheet consume the Escape they close on", () => {
  it("Menu", () => {
    const onClose = vi.fn();
    render(
      <span>
        <Menu open onClose={onClose}>
          <button type="button">Item</button>
        </Menu>
      </span>,
    );
    const ok = fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(ok).toBe(false);
    // Other keys are not touched.
    expect(fireEvent.keyDown(document.body, { key: "ArrowDown" })).toBe(true);
  });

  it("Sheet", () => {
    const onClose = vi.fn();
    render(
      <Sheet open onClose={onClose} label="Details">
        <p>Body</p>
      </Sheet>,
    );
    const ok = fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(ok).toBe(false);
  });

  it("a closed Sheet leaves Escape alone", () => {
    render(
      <Sheet open={false} onClose={vi.fn()} label="Details">
        <p>Body</p>
      </Sheet>,
    );
    expect(fireEvent.keyDown(document.body, { key: "Escape" })).toBe(true);
  });
});
