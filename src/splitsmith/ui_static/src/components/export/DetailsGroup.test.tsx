/**
 * Details: the "Made with splitsmith" credit is one checkbox, shown only
 * when the closing card is drawn, on by default.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DetailsGroup } from "@/components/export/DetailsGroup";
import { DEFAULT_EXPORT_SETTINGS, type ExportSettings } from "@/lib/exportPresets";

function renderDetails(settings: ExportSettings, patch = vi.fn()) {
  render(
    <DetailsGroup
      settings={settings}
      patch={patch}
      busy={false}
      projectName="Bromma"
      onProjectName={() => {}}
      exportsDir={null}
      descriptionLead=""
      onDescriptionLead={() => {}}
      youtubeSettings={null}
      onYouTubeSettingsChange={() => {}}
      matchName="Bromma"
    />,
  );
  return patch;
}

const MP4: ExportSettings = { ...DEFAULT_EXPORT_SETTINGS, mode: "single", outputFormat: "mp4" };

describe("the credit checkbox", () => {
  it("is hidden without a closing card", () => {
    renderDetails({ ...MP4, renderOptions: { ...MP4.renderOptions, titlePage: true } });
    expect(screen.queryByLabelText("Made with splitsmith")).toBeNull();
  });

  it("is on by default and turns the credit off", () => {
    const patch = renderDetails({ ...MP4, renderOptions: { ...MP4.renderOptions, closingCard: true } });
    const box = screen.getByLabelText("Made with splitsmith") as HTMLInputElement;
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(patch).toHaveBeenCalledWith({
      renderOptions: { ...MP4.renderOptions, closingCard: true, madeWith: false },
    });
  });
});
