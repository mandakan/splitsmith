/**
 * The Look gallery (spec 2026-09-15 s2): tiles per slot, the selected
 * variant's parameters under its row, and only what the mode and format
 * can draw. Folds in the render panel's cases (the two-boolean title
 * page mapping, the seconds fields, the per-format rules).
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LookGallery } from "@/components/export/LookGallery";
import { DEFAULT_EXPORT_SETTINGS, type ExportSettings } from "@/lib/exportPresets";

function setup(over: Partial<ExportSettings> = {}, bareHints = {}) {
  const settings = { ...DEFAULT_EXPORT_SETTINGS, ...over };
  const patch = vi.fn();
  const view = render(<LookGallery settings={settings} patch={patch} busy={false} bareHints={bareHints} />);
  return { settings, patch, user: userEvent.setup(), container: view.container };
}

const tile = (slot: string, name: string) =>
  within(screen.getByRole("radiogroup", { name: slot })).getByRole("radio", { name });

const groups = () => screen.getAllByRole("radiogroup").map((g) => g.getAttribute("aria-label"));

describe("LookGallery", () => {
  it("on MP4 offers every slot, each tile with its thumbnail, and the transition stays hidden", () => {
    setup({ outputFormat: "mp4" });
    expect(groups()).toEqual(["Title page", "Stage card", "Closing card", "Stage summary", "Overlay"]);
    expect(tile("Title page", "None")).toBeChecked();
    expect(tile("Stage card", "Slate").querySelector("img")).toHaveAttribute(
      "src",
      expect.stringMatching(/stage-card-slate/),
    );
  });

  it("on FCPXML offers the stage card, the overlay and the transition only", () => {
    setup({ outputFormat: "fcpxml" });
    expect(groups()).toEqual(["Stage card", "Overlay", "Transition"]);
    expect(tile("Transition", "Hard cut")).toBeChecked();
  });

  it("on FCP 7 XML offers the overlay only", () => {
    setup({ outputFormat: "fcp7xml" });
    expect(groups()).toEqual(["Overlay"]);
  });

  it("on the grid offers the cards and the grid overlay with its hold, never the summary", () => {
    setup({ mode: "compare", gridOverlay: true, gridHoldSeconds: 2 });
    expect(groups()).toEqual(["Title page", "Stage card", "Closing card", "Overlay"]);
    expect(tile("Overlay", "Shot counter")).toBeChecked();
    expect(screen.getByLabelText("Grid summary hold seconds")).toHaveValue(2);
    expect(screen.getByText(/Per-tile shot counter/)).toBeInTheDocument();
    expect(screen.queryByText(/codec is under Output/)).toBeNull();
  });

  it("in single mode the overlay has no hold field", () => {
    setup({ outputFormat: "mp4", includeOverlay: true });
    expect(tile("Overlay", "Shot counter")).toBeChecked();
    expect(screen.queryByLabelText("Grid summary hold seconds")).toBeNull();
  });

  it("selecting a tile patches through the slot, keeping the rest of the options", async () => {
    const { user, patch, settings } = setup({ outputFormat: "mp4" });
    await user.click(tile("Stage card", "Lower third"));
    expect(patch).toHaveBeenCalledWith({ renderOptions: { ...settings.renderOptions, stageCardStyle: "lower-third" } });
    await user.click(tile("Title page", "Title page"));
    expect(patch).toHaveBeenLastCalledWith({ renderOptions: { ...settings.renderOptions, titlePage: true } });
    // Clicking the selected tile again is not a change.
    patch.mockClear();
    await user.click(tile("Closing card", "None"));
    expect(patch).not.toHaveBeenCalled();
  });

  it("shows the selected variant's parameters and help, and none for the off tile", () => {
    const { patch } = setup({
      outputFormat: "mp4",
      renderOptions: { ...DEFAULT_EXPORT_SETTINGS.renderOptions, stageCardStyle: "slate", stageCardDurationSeconds: 1.5 },
    });
    expect(screen.getByLabelText("Stage card seconds")).toHaveValue(1.5);
    expect(screen.getByText(/on its own card before each stage/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Title page seconds")).toBeNull();
    // The prop never updates under a mock, so one change event rather than typing.
    fireEvent.change(screen.getByLabelText("Stage card seconds"), { target: { value: "2" } });
    expect(patch).toHaveBeenLastCalledWith({
      renderOptions: expect.objectContaining({ stageCardDurationSeconds: 2 }),
    });
  });

  it("the summary hold tile turns the hold on at 3 s", async () => {
    const { user, patch } = setup({ outputFormat: "mp4" });
    await user.click(tile("Stage summary", "Summary hold"));
    expect(patch).toHaveBeenCalledWith({ renderOptions: expect.objectContaining({ summaryHoldSeconds: 3 }) });
  });

  it("appends the bare hint to the slot's help only while its variant is on", () => {
    setup({ outputFormat: "mp4", includeOverlay: true }, { overlay: "Skipped on 2 stages without splits." });
    expect(screen.getByText(/Skipped on 2 stages without splits\./)).toBeInTheDocument();
    expect(screen.queryByText(/Time and scoring only/)).toBeNull();
  });

  it("disables every tile and input while busy", () => {
    render(
      <LookGallery
        settings={{ ...DEFAULT_EXPORT_SETTINGS, outputFormat: "mp4", includeOverlay: true }}
        patch={vi.fn()}
        busy
        bareHints={{}}
      />,
    );
    for (const r of screen.getAllByRole("radio")) expect(r).toBeDisabled();
  });

  it("never paints a coloured fill and has no primary action", () => {
    const { container } = setup({ outputFormat: "mp4" });
    expect(container.querySelector(".btn-primary")).toBeNull();
    expect(container.querySelector("[class*='bg-led']")).toBeNull();
  });
});
