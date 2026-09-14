import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Segmented } from "./Segmented";

describe("Segmented", () => {
  it("presses the current option and reports a click; a disabled option keeps its reason", async () => {
    const onChange = vi.fn();
    render(
      <Segmented
        label="Output"
        value="single"
        onChange={onChange}
        options={[
          { value: "single", label: "Timeline" },
          { value: "trims", label: "Trims only" },
          { value: "compare", label: "Compare grid", disabled: true, title: "Needs two shooters" },
        ]}
      />,
    );
    expect(screen.getByRole("button", { name: "Timeline" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Trims only" }));
    expect(onChange).toHaveBeenCalledWith("trims");
    const grid = screen.getByRole("button", { name: "Compare grid" });
    expect(grid).toBeDisabled();
    expect(grid).toHaveAttribute("title", "Needs two shooters");
  });
});
