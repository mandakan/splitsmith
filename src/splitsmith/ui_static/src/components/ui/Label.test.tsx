import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Label } from "./Label";

describe("Label", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the one tracked-caps style", () => {
    render(<Label>Avg split</Label>);
    const el = screen.getByText("Avg split");
    expect(el.className).toMatch(/font-mono/);
    expect(el.className).toMatch(/uppercase/);
    expect(el.className).toMatch(/tracking-\[0\.08em\]/);
    expect(el.className).toMatch(/text-\[11px\]/);
  });

  it("warns in dev when asked to carry a sentence", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(<Label>Footage attached and participating in this match</Label>);
    expect(warn).toHaveBeenCalledWith(expect.stringMatching(/Label.*three words/));
  });

  it("tones", () => {
    render(<Label tone="accent">Results</Label>);
    expect(screen.getByText("Results").className).toMatch(/accent-mode/);
  });
});
