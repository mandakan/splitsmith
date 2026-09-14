import { fireEvent, render, screen, within } from "@testing-library/react";
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

function choice(group: string, label: string): HTMLElement {
  return within(screen.getByRole("group", { name: group })).getByRole("button", { name: label });
}

describe("RenderOptionsPanel", () => {
  it("emits the whole value with one field changed, never mutating the input", () => {
    const { onChange } = renderPanel();
    fireEvent.click(choice("Title page", "Opening"));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_RENDER_OPTIONS, titlePage: true, closingCard: false });
    expect(DEFAULT_RENDER_OPTIONS.titlePage).toBe(false);
  });

  it("maps the title page choice onto the two booleans, both ways", () => {
    const { onChange } = renderPanel();
    fireEvent.click(choice("Title page", "Opening + closing"));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_RENDER_OPTIONS, titlePage: true, closingCard: true });
    fireEvent.click(choice("Title page", "Closing"));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_RENDER_OPTIONS, titlePage: false, closingCard: true });
    // A closing-only value reads back as exactly that, never as "both".
    render(
      <RenderOptionsPanel
        value={{ ...DEFAULT_RENDER_OPTIONS, closingCard: true }}
        onChange={vi.fn()}
        surface="single"
        outputFormat="mp4"
      />,
    );
    const closing = screen.getAllByRole("button", { name: "Closing" }).at(-1);
    expect(closing).toHaveAttribute("aria-pressed", "true");
  });

  it("maps the stage card choice and the seconds fields onto the value", () => {
    const on: RenderOptions = { ...DEFAULT_RENDER_OPTIONS, titlePage: true, stageCardStyle: "slate" };
    const { onChange } = renderPanel({ value: on });
    fireEvent.click(choice("Stage card style", "Lower third"));
    expect(onChange).toHaveBeenLastCalledWith({ ...on, stageCardStyle: "lower-third" });
    fireEvent.change(screen.getByLabelText("Stage card seconds"), { target: { value: "2.5" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...on, stageCardDurationSeconds: 2.5 });
    fireEvent.change(screen.getByLabelText("Title page info line"), { target: { value: "Level II" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...on, titleInfo: "Level II" });
    fireEvent.change(screen.getByLabelText("Summary hold seconds"), { target: { value: "3" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...on, summaryHoldSeconds: 3 });
  });

  it("on FCPXML keeps the stage card live but disables the match cards and the summary, saying why", () => {
    renderPanel({ outputFormat: "fcpxml" });
    expect(choice("Title page", "Opening")).toBeDisabled();
    expect(choice("Stage card style", "Slate")).toBeEnabled();
    expect(screen.getByLabelText("Summary hold seconds")).toBeDisabled();
    expect(screen.getAllByText("Renders in MP4 output only.")).toHaveLength(2);
  });

  it("on FCP 7 XML disables the stage card too", () => {
    renderPanel({ outputFormat: "fcp7xml" });
    expect(choice("Stage card style", "Slate")).toBeDisabled();
    expect(screen.getByText(/FCP 7 XML has no title track/)).toBeInTheDocument();
  });

  it("offers no summary hold on the grid surface", () => {
    renderPanel({ surface: "grid" });
    expect(screen.queryByLabelText("Summary hold seconds")).toBeNull();
    expect(screen.queryByText("Renders in MP4 output only.")).toBeNull();
  });

  it("shows the info line and the seconds only once a card is on", () => {
    renderPanel();
    expect(screen.queryByLabelText("Title page info line")).toBeNull();
    expect(screen.queryByLabelText("Title page seconds")).toBeNull();
    expect(screen.queryByLabelText("Stage card seconds")).toBeNull();
    renderPanel({ value: { ...DEFAULT_RENDER_OPTIONS, titlePage: true, stageCardStyle: "slate" } });
    expect(screen.getByLabelText("Title page info line")).toBeEnabled();
    expect(screen.getByLabelText("Title page seconds")).toBeEnabled();
    expect(screen.getByLabelText("Stage card seconds")).toBeEnabled();
  });

  it("never paints a coloured fill and has no primary action of its own", () => {
    const { container } = renderPanel({ value: { ...DEFAULT_RENDER_OPTIONS, titlePage: true } });
    expect(container.querySelector(".btn-primary")).toBeNull();
    expect(container.querySelector("[class*='bg-led']")).toBeNull();
    // Every button is a Segmented choice, none submits anything.
    for (const button of container.querySelectorAll("button")) {
      expect(button).toHaveAttribute("aria-pressed");
    }
  });
});
