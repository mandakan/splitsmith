import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_CAM_OPTIONS, type CameraChoice } from "@/lib/camOptions";

import { CamOptionsPanel } from "./CamOptionsPanel";

const CHOICES: CameraChoice[] = [
  { value: "primary", label: "Primary" },
  { value: "hand", label: "Handheld" },
  { value: "head", label: "Head cam" },
];

function choice(group: string, label: string): HTMLElement {
  return within(screen.getByRole("group", { name: group })).getByRole(
    "button",
    { name: label },
  );
}

function panel(over: Partial<Parameters<typeof CamOptionsPanel>[0]> = {}) {
  const onChange = vi.fn();
  const utils = render(
    <CamOptionsPanel
      value={DEFAULT_CAM_OPTIONS}
      onChange={onChange}
      secondaryCount={2}
      choices={CHOICES}
      savedLabel="Handheld"
      editing
      {...over}
    />,
  );
  return { onChange, ...utils };
}

describe("CamOptionsPanel", () => {
  it("renders nothing for a shooter with no synced secondary", () => {
    const { container } = panel({ secondaryCount: 0 });
    expect(container.innerHTML).toBe("");
  });

  it("starts on the saved default and names what that is", () => {
    panel();
    expect(choice("Main camera", "Saved default")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByText(/The shooter's saved camera \(Handheld\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("2 synced cameras")).toBeInTheDocument();
  });

  it("picks the main camera and an inset; the corner and size show only with an inset", () => {
    const { onChange, rerender } = panel();
    expect(screen.queryByRole("group", { name: "Inset corner" })).toBeNull();
    fireEvent.click(choice("Main camera", "Handheld"));
    expect(onChange).toHaveBeenLastCalledWith({
      ...DEFAULT_CAM_OPTIONS,
      mainCamera: "hand",
    });
    fireEvent.click(choice("Inset camera", "Head cam"));
    expect(onChange).toHaveBeenLastCalledWith({
      ...DEFAULT_CAM_OPTIONS,
      insetCamera: "head",
    });
    rerender(
      <CamOptionsPanel
        value={{ ...DEFAULT_CAM_OPTIONS, insetCamera: "head" }}
        onChange={onChange}
        secondaryCount={2}
        choices={CHOICES}
        savedLabel="Handheld"
        editing
      />,
    );
    fireEvent.click(choice("Inset corner", "Top left"));
    expect(onChange).toHaveBeenLastCalledWith({
      ...DEFAULT_CAM_OPTIONS,
      insetCamera: "head",
      insetCorner: "top-left",
    });
    fireEvent.click(choice("Inset camera", "None"));
    expect(onChange).toHaveBeenLastCalledWith({
      ...DEFAULT_CAM_OPTIONS,
      insetCamera: null,
    });
  });

  it("offers the other angles only on an editing timeline", () => {
    panel({ editing: false });
    expect(screen.queryByRole("group", { name: "Other angles" })).toBeNull();
  });

  it("has no primary action and no coloured fill", () => {
    const { container } = panel();
    expect(container.querySelector(".btn-primary")).toBeNull();
    expect(container.querySelector("[class*='bg-led']")).toBeNull();
    for (const button of container.querySelectorAll("button")) {
      expect(button).toHaveAttribute("aria-pressed");
    }
  });

  it("on the grid states the main camera and offers only the inset", () => {
    panel({ grid: true, secondaryCount: 2, editing: true });
    expect(screen.queryByRole("group", { name: "Main camera" })).toBeNull();
    expect(screen.getByText(/Each shooter's saved camera/)).toBeInTheDocument();
    expect(
      screen.getByText("2 shooters with more than one camera"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Inset camera" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Other angles" })).toBeNull();
  });
});
