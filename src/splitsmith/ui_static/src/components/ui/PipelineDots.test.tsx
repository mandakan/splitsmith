import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PipelineDots } from "./PipelineDots";

describe("PipelineDots", () => {
  it("is one labelled image with one dot per stage and no colour-only meaning", () => {
    render(<PipelineDots states={["done", "progress", "todo"]} label="1 of 3 stages audited" />);
    const img = screen.getByRole("img", { name: "1 of 3 stages audited" });
    const dots = img.querySelectorAll("[data-state]");
    expect(dots).toHaveLength(3);
    expect(dots[0]).toHaveAttribute("data-state", "done");
    expect(dots[2].className).toMatch(/border/);
  });
});
