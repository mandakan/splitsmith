import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Stat, StatStrip } from "./Stat";

describe("Stat", () => {
  it("renders label, numeral and unit", () => {
    render(<Stat label="Avg split" value="0.386" unit="s" />);
    expect(screen.getByText("Avg split").className).toMatch(/uppercase/);
    expect(screen.getByText("0.386").className).toMatch(/numeral/);
    expect(screen.getByText("s")).toBeInTheDocument();
  });

  it("dim tone marks a provisional figure", () => {
    render(<Stat label="Draw" value="1.93" tone="dim" />);
    expect(screen.getByText("1.93").className).toMatch(/text-muted/);
  });
});

describe("StatStrip", () => {
  it("cannot be crushed by a flex scroll column and leads with its first cell on mobile", () => {
    const { container } = render(
      <StatStrip lead>
        <Stat label="Stage time" value="32.09" unit="s" />
        <Stat label="Shots" value="30" />
      </StatStrip>,
    );
    const root = container.firstElementChild!;
    expect(root).toHaveClass("shrink-0");
    expect(root.firstElementChild).toHaveClass("col-span-2");
    expect(root.firstElementChild).toHaveClass("md:col-span-1");
  });
});
