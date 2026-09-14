import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_CAM_OPTIONS } from "@/lib/camOptions";

import { CamOptionsPanel } from "./CamOptionsPanel";

function choice(group: string, label: string): HTMLElement {
  return within(screen.getByRole("group", { name: group })).getByRole("button", { name: label });
}

describe("CamOptionsPanel", () => {
  it("renders nothing for a shooter with no synced secondary", () => {
    const { container } = render(
      <CamOptionsPanel value={DEFAULT_CAM_OPTIONS} onChange={vi.fn()} secondaryCount={0} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("emits the whole value with one field changed", () => {
    const onChange = vi.fn();
    render(<CamOptionsPanel value={DEFAULT_CAM_OPTIONS} onChange={onChange} secondaryCount={2} />);
    expect(screen.getByText("2 synced cameras")).toBeInTheDocument();
    fireEvent.click(choice("Secondary cam layout", "Picture-in-picture"));
    expect(onChange).toHaveBeenLastCalledWith({ includeSecondaries: true, pipLayout: "pip-corners" });
    fireEvent.click(choice("Secondary cams", "Primary only"));
    expect(onChange).toHaveBeenLastCalledWith({ includeSecondaries: false, pipLayout: "stacked" });
  });

  it("hides the layout while the cams are off, and is singular for one cam", () => {
    render(
      <CamOptionsPanel
        value={{ includeSecondaries: false, pipLayout: "stacked" }}
        onChange={vi.fn()}
        secondaryCount={1}
      />,
    );
    expect(screen.getByText("1 synced camera")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Secondary cam layout" })).toBeNull();
    expect(screen.getByText(/Primary only; the cam trims stay out/)).toBeInTheDocument();
  });

  it("has no primary action and no coloured fill", () => {
    const { container } = render(
      <CamOptionsPanel value={DEFAULT_CAM_OPTIONS} onChange={vi.fn()} secondaryCount={1} />,
    );
    expect(container.querySelector(".btn-primary")).toBeNull();
    expect(container.querySelector("[class*='bg-led']")).toBeNull();
    for (const button of container.querySelectorAll("button")) {
      expect(button).toHaveAttribute("aria-pressed");
    }
  });
});
