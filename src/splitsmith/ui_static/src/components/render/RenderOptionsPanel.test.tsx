import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_RENDER_OPTIONS, type RenderOptions } from "@/lib/renderOptions";

import { RenderOptionsPanel } from "./RenderOptionsPanel";

function renderPanel(overrides: Partial<Parameters<typeof RenderOptionsPanel>[0]> = {}) {
  const onChange = vi.fn();
  const utils = render(
    <RenderOptionsPanel
      value={DEFAULT_RENDER_OPTIONS}
      onChange={onChange}
      surface="single"
      outputFormat="mp4"
      {...overrides}
    />,
  );
  return { onChange, ...utils };
}

describe("RenderOptionsPanel", () => {
  it("emits the whole value with one field changed, never mutating the input", () => {
    const { onChange } = renderPanel();
    fireEvent.click(screen.getByLabelText("Open with the match name"));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_RENDER_OPTIONS, titlePage: true });
    expect(DEFAULT_RENDER_OPTIONS.titlePage).toBe(false);
  });

  it("maps the stage card select and the seconds fields onto the value", () => {
    const on: RenderOptions = { ...DEFAULT_RENDER_OPTIONS, titlePage: true, stageCardStyle: "slate" };
    const { onChange } = renderPanel({ value: on });
    fireEvent.change(screen.getByLabelText("Stage card style"), { target: { value: "lower-third" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...on, stageCardStyle: "lower-third" });
    fireEvent.change(screen.getByLabelText("Stage card seconds"), { target: { value: "2.5" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...on, stageCardDurationSeconds: 2.5 });
    fireEvent.change(screen.getByLabelText("Title page info line"), { target: { value: "Level II" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...on, titleInfo: "Level II" });
    fireEvent.change(screen.getByLabelText("Summary hold seconds"), { target: { value: "3" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...on, summaryHoldSeconds: 3 });
  });

  it("disables everything and says why on an XML format", () => {
    renderPanel({ outputFormat: "fcpxml" });
    expect(screen.getByTestId("render-options-note")).toHaveTextContent("MP4 output only");
    expect(screen.getByLabelText("Open with the match name")).toBeDisabled();
    expect(screen.getByLabelText("Stage card style")).toBeDisabled();
    expect(screen.getByLabelText("Summary hold seconds")).toBeDisabled();
  });

  it("offers no summary hold on the grid surface", () => {
    renderPanel({ surface: "grid" });
    expect(screen.queryByLabelText("Summary hold seconds")).toBeNull();
    expect(screen.queryByTestId("render-options-note")).toBeNull();
  });

  it("keeps the info line and title seconds inert until a title card is on", () => {
    renderPanel();
    expect(screen.getByLabelText("Title page info line")).toBeDisabled();
    expect(screen.getByLabelText("Title page seconds")).toBeDisabled();
    expect(screen.getByLabelText("Stage card seconds")).toBeDisabled();
  });

  it("never paints a coloured fill and has no primary action of its own", () => {
    const { container } = renderPanel({ value: { ...DEFAULT_RENDER_OPTIONS, titlePage: true } });
    expect(container.querySelector(".btn-primary")).toBeNull();
    expect(container.querySelector("[class*='bg-led ']")).toBeNull();
    expect(container.querySelectorAll("button").length).toBe(0);
  });
});
