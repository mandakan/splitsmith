/**
 * Palette suggestions (#1273): the warning when the accent blends into the
 * footage, one-click palettes from each source, the footage source off when
 * there is no footage on this machine.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PaletteSuggestions } from "@/components/export/PaletteSuggestions";
import { api, type Rgb, type StoredLookBody } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, paletteSources: vi.fn() } };
});

const COLORS: Record<string, Rgb> = {
  ink: [244, 244, 245],
  ink_2: [201, 204, 210],
  muted: [142, 147, 155],
  subtle: [107, 112, 121],
  surface: [20, 23, 28],
  rule: [38, 43, 51],
  stroke: [10, 11, 13],
  accent: [96, 116, 58],
  accent_fill: [80, 96, 48],
  accent_text: [255, 255, 255],
  split: [251, 191, 36],
  split_good: [74, 222, 128],
};
const DRAFT: StoredLookBody = { label: "Club", base: "splitsmith", colors: COLORS, accent_series: [], styles: {} };
const RANGE = [
  { rgb: [78, 104, 52] as Rgb, share: 0.38 },
  { rgb: [122, 98, 70] as Rgb, share: 0.27 },
  { rgb: [176, 160, 128] as Rgb, share: 0.2 },
  { rgb: [168, 190, 214] as Rgb, share: 0.15 },
];

afterEach(() => vi.clearAllMocks());

describe("PaletteSuggestions", () => {
  it("warns that an olive accent blends into the range and offers one that stands out", async () => {
    vi.mocked(api.paletteSources).mockResolvedValue({ footage: RANGE, average: [118, 122, 92], logo: [] });
    const setDraft = vi.fn();
    render(<PaletteSuggestions draft={DRAFT} setDraft={setDraft} slug="me" stageNumber={2} />);
    expect(await screen.findByText(/blends into this footage/)).toBeTruthy();
    expect(api.paletteSources).toHaveBeenCalledWith("me", [2]);
    fireEvent.click(screen.getByRole("button", { name: /^Use #/ }));
    const next = setDraft.mock.calls[0][0] as StoredLookBody;
    expect(next.colors.accent).not.toEqual(COLORS.accent);
    expect(next.accent_series).toHaveLength(6);
    expect(next.colors.ink).toEqual(COLORS.ink);
  });

  it("applies a ready-made palette in one click", async () => {
    vi.mocked(api.paletteSources).mockResolvedValue({ footage: [], average: null, logo: [] });
    const setDraft = vi.fn();
    render(<PaletteSuggestions draft={DRAFT} setDraft={setDraft} slug="me" stageNumber={2} />);
    await waitFor(() => expect(api.paletteSources).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Ready-made" }));
    fireEvent.click(screen.getByRole("button", { name: "Use the Indoor range palette" }));
    const next = setDraft.mock.calls[0][0] as StoredLookBody;
    expect(next.colors.accent).toEqual([0, 190, 232]);
    expect(screen.getByRole("button", { name: "This footage" }).hasAttribute("disabled")).toBe(true);
  });

  it("offers palettes from the club logo's colours", async () => {
    vi.mocked(api.paletteSources).mockResolvedValue({
      footage: RANGE,
      average: [118, 122, 92],
      logo: [
        { rgb: [255, 255, 255], share: 0.6 },
        { rgb: [10, 60, 200], share: 0.4 },
      ],
    });
    render(<PaletteSuggestions draft={DRAFT} setDraft={vi.fn()} slug="me" stageNumber={2} />);
    await screen.findByText(/blends into this footage/);
    fireEvent.click(screen.getByRole("button", { name: "Club logo" }));
    // White is no accent; the blue is.
    expect(screen.getByRole("button", { name: "Use the Logo #0a3cc8 palette" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Use the Logo #ffffff palette" })).toBeNull();
  });
});
