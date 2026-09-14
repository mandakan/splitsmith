import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Chip } from "./Chip";

describe("Chip", () => {
  it("is a neutral pill with a coloured tick, not a coloured fill", () => {
    render(<Chip tick="movement">Movement</Chip>);
    const chip = screen.getByText("Movement");
    expect(chip.className).toMatch(/rounded-full/);
    expect(chip.className).not.toMatch(/bg-beep/);
    const tick = chip.querySelector("[data-tick]");
    expect(tick?.className).toMatch(/bg-beep/);
  });

  it("warn tone tints border and text", () => {
    render(<Chip tone="warn">4 flags</Chip>);
    expect(screen.getByText("4 flags").className).toMatch(/text-live/);
  });
});
