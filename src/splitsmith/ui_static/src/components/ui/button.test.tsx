import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "./button";

describe("Button budget", () => {
  it("primary is the Antonio led-fill recipe", () => {
    render(<Button variant="primary">Save</Button>);
    expect(screen.getByRole("button").className).toMatch(/btn-led-fill/);
  });

  it("default is neutral Geist, not red", () => {
    render(<Button>Compare</Button>);
    const c = screen.getByRole("button").className;
    expect(c).not.toMatch(/bg-led/);
    expect(c).toMatch(/border-rule-strong/);
  });

  it("destructive is an outline, never a fill", () => {
    render(<Button variant="destructive">Remove</Button>);
    const c = screen.getByRole("button").className;
    expect(c).not.toMatch(/bg-destructive/);
    expect(c).toMatch(/text-led-text/);
  });
});
