import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LogoGuide } from "@/components/shooters/LogoGuide";

function spot(container: HTMLElement, name: string): HTMLElement {
  return container.querySelector(`[data-spot="${name}"]`) as HTMLElement;
}

describe("LogoGuide", () => {
  it("names every empty spot by whose logo goes there", () => {
    render(<LogoGuide />);
    for (const label of ["Your brand", "Shooter logo", "Event logo"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("draws a logo that is set in its own spot", () => {
    const { container } = render(<LogoGuide shooter="/s.png" brand="/b.png" />);
    expect(spot(container, "shooter").querySelector("img")?.getAttribute("src")).toBe("/s.png");
    expect(spot(container, "brand").querySelector("img")?.getAttribute("src")).toBe("/b.png");
    expect(spot(container, "event").querySelector("img")).toBeNull();
  });

  it("dims the other spots when one is highlighted", () => {
    const { container } = render(<LogoGuide highlight="shooter" />);
    expect(spot(container, "shooter").className).not.toContain("opacity-30");
    expect(spot(container, "brand").className).toContain("opacity-30");
    expect(spot(container, "event").className).toContain("opacity-30");
  });
});
