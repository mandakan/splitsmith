import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_CAM_OPTIONS } from "@/lib/camOptions";

import { CamOptionsPanel } from "./CamOptionsPanel";

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
    fireEvent.change(screen.getByLabelText("Secondary cam layout"), { target: { value: "pip-corners" } });
    expect(onChange).toHaveBeenLastCalledWith({ includeSecondaries: true, pipLayout: "pip-corners" });
    fireEvent.click(screen.getByLabelText("Ship the per-cam trims with the primary"));
    expect(onChange).toHaveBeenLastCalledWith({ includeSecondaries: false, pipLayout: "stacked" });
  });

  it("keeps the layout inert while the cams are off, and singular for one cam", () => {
    render(
      <CamOptionsPanel
        value={{ includeSecondaries: false, pipLayout: "stacked" }}
        onChange={vi.fn()}
        secondaryCount={1}
      />,
    );
    expect(screen.getByText("1 synced camera")).toBeInTheDocument();
    expect(screen.getByLabelText("Secondary cam layout")).toBeDisabled();
  });

  it("has no button and no coloured fill", () => {
    const { container } = render(
      <CamOptionsPanel value={DEFAULT_CAM_OPTIONS} onChange={vi.fn()} secondaryCount={1} />,
    );
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelector("[class*='bg-led']")).toBeNull();
  });
});
