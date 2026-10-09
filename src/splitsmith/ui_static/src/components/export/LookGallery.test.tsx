/**
 * LookGallery with a catalog (#1246): the Look tiles, the Style control
 * under a card slot and the Look's stings as transition tiles.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LookGallery } from "@/components/export/LookGallery";
import type { LookInfo } from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS, type ExportSettings } from "@/lib/exportPresets";
import { FAMILIES } from "@/test/transitionFamilies";

const CATALOG: LookInfo[] = [
  {
    name: "splitsmith",
    label: "Splitsmith",
    source: "shipped",
    accent_series: [],
    preview: "/api/looks/splitsmith/preview/look.png",
    slots: {
      title_page: [{ name: "default", preview: null }, { name: "rise", preview: "/api/looks/splitsmith/preview/title_page-rise.png" }],
      slate: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      lower_third: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      summary: [],
      closing: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      transition: [{ name: "wipe", preview: "/api/looks/splitsmith/preview/transition-wipe.png" }],
    },
  },
  {
    name: "club",
    label: "Club",
    source: "user",
    accent_series: [],
    preview: null,
    slots: {
      title_page: [{ name: "default", preview: null }],
      slate: [{ name: "default", preview: null }],
      lower_third: [{ name: "default", preview: null }],
      summary: [],
      closing: [{ name: "default", preview: null }],
      transition: [],
    },
  },
];

function mount(settings: ExportSettings, looks: LookInfo[] = CATALOG) {
  const patch = vi.fn();
  render(
    <LookGallery settings={settings} patch={patch} busy={false} bareHints={{}} looks={looks} transitions={FAMILIES} />,
  );
  return patch;
}

const MP4: ExportSettings = { ...DEFAULT_EXPORT_SETTINGS, outputFormat: "mp4" };

describe("LookGallery with a catalog", () => {
  it("says what speed colours do under a chosen overlay style's toggles", () => {
    const looks: LookInfo[] = [
      {
        ...CATALOG[0],
        slots: {
          ...CATALOG[0].slots,
          overlay: [
            { name: "default", preview: null },
            { name: "plate", preview: null, positions: ["bottom-left", "top-right"] },
          ],
        },
      },
    ];
    mount(
      { ...MP4, includeOverlay: true, overlayStyle: { ...MP4.overlayStyle, variant: "plate" } },
      looks,
    );
    expect(screen.getByRole("checkbox", { name: "Speed colours" })).not.toBeChecked();
    expect(screen.getByText(/off draws every split in one colour/)).toBeInTheDocument();
  });

  it("offers the installed Looks as tiles and writes the choice", async () => {
    const user = userEvent.setup();
    const patch = mount(MP4);
    const looks = screen.getByRole("radiogroup", { name: "Look" });
    expect(within(looks).getAllByRole("radio").map((r) => r.textContent)).toEqual(["Splitsmith", "Club"]);
    await user.click(within(looks).getByRole("radio", { name: "Club" }));
    expect(patch).toHaveBeenCalledWith({ look: "club" });
  });

  it("shows no Look tiles when only one Look is installed", () => {
    mount(MP4, [CATALOG[0]]);
    expect(screen.queryByRole("radiogroup", { name: "Look" })).toBeNull();
  });

  it("shows a Style under a card that is on when the Look has more than one variant, and writes it", async () => {
    const user = userEvent.setup();
    const on = { ...MP4, renderOptions: { ...MP4.renderOptions, titlePage: true } };
    const patch = mount(on);
    const style = screen.getByRole("group", { name: "Title page style" });
    expect(within(style).getAllByRole("button").map((r) => r.textContent)).toEqual(["Default", "Rise"]);
    await user.click(within(style).getByRole("button", { name: "Rise" }));
    expect(patch).toHaveBeenCalledWith({ titlePageVariant: "rise" });
  });

  it("hides the Style while the card is off, and for a Look with one variant", () => {
    mount(MP4);
    expect(screen.queryByRole("group", { name: "Title page style" })).toBeNull();
    const club = { ...MP4, look: "club", renderOptions: { ...MP4.renderOptions, titlePage: true } };
    mount(club);
    expect(screen.queryByRole("group", { name: "Title page style" })).toBeNull();
  });

  it("offers the Look's sting among the transitions with its API preview", () => {
    mount(MP4);
    const transitions = screen.getByRole("radiogroup", { name: "Transition" });
    const sting = within(transitions).getByRole("radio", { name: "Wipe sting" });
    expect(sting.querySelector("img")?.getAttribute("src")).toContain("/api/looks/splitsmith/preview/transition-wipe.png");
  });
});


describe("transition families (#1259)", () => {
  it("offers the families as tiles with their looping previews", () => {
    mount(MP4);
    const row = screen.getByRole("radiogroup", { name: "Transition" });
    expect(within(row).getAllByRole("radio").map((r) => r.textContent)).toEqual([
      "Hard cut",
      "Fade",
      "Slide",
      "Wind",
      "Circle",
      "Wipe sting",
    ]);
    expect(within(row).getByRole("radio", { name: "Wind" }).querySelector("img")?.getAttribute("src")).toContain(
      "/api/looks/_transitions/preview/wind.webp",
    );
  });

  it("shows a Direction under a family with more than one, and writes the kind", async () => {
    const user = userEvent.setup();
    const patch = mount({ ...MP4, transitionKind: "hlwind" });
    const direction = screen.getByRole("group", { name: "Transition direction" });
    expect(within(direction).getAllByRole("button").map((b) => b.textContent)).toEqual(["Left", "Right", "Up", "Down"]);
    expect(within(direction).getByRole("button", { name: "Left" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(direction).getByRole("button", { name: "Up" }));
    expect(patch).toHaveBeenCalledWith({ transitionKind: "vuwind" });
  });

  it("shows no Direction for a single-kind family or the cut", () => {
    mount({ ...MP4, transitionKind: "fade" });
    expect(screen.queryByRole("group", { name: "Transition direction" })).toBeNull();
  });
});
