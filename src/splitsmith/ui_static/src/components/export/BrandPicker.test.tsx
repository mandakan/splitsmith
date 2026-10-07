/**
 * Your brand in the Look editor (the branding work): a logo and a line the
 * title page and the closing card draw in their top left corner. Desktop holds
 * the logo file; hosted keeps the line only for now.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BrandPicker } from "@/components/export/BrandPicker";
import type { StoredLookBody } from "@/lib/api";
import { isDirty } from "@/lib/lookEditor";

const DRAFT: StoredLookBody = {
  label: "Club",
  base: "splitsmith",
  colors: {},
  accent_series: [],
  styles: {},
};

describe("BrandPicker", () => {
  it("uploads a logo and names it on the draft, with a line under it", async () => {
    const upload = vi
      .fn()
      .mockResolvedValue({
        logo: "brand-0123456789ab.png",
        url: "/api/looks/club/brand/brand-0123456789ab.png",
      });
    let draft = DRAFT;
    const setDraft = vi.fn((d: StoredLookBody) => {
      draft = d;
    });
    const { rerender } = render(
      <BrandPicker
        name="club"
        draft={draft}
        setDraft={setDraft}
        upload={upload}
        hosted={false}
      />,
    );
    const file = new File([new Uint8Array([137, 80, 78, 71])], "club.png", {
      type: "image/png",
    });
    fireEvent.change(screen.getByLabelText("Brand logo file"), {
      target: { files: [file] },
    });
    await waitFor(() => expect(setDraft).toHaveBeenCalled());
    expect(draft.brand).toEqual({ logo: "brand-0123456789ab.png", line: "" });
    rerender(
      <BrandPicker
        name="club"
        draft={draft}
        setDraft={setDraft}
        upload={upload}
        hosted={false}
      />,
    );
    expect(
      screen.getByRole("img", { name: "Brand logo" }).getAttribute("src"),
    ).toMatch(/brand-0123456789ab\.png$/);
    fireEvent.change(screen.getByLabelText("Brand line"), {
      target: { value: "Bromma PK" },
    });
    expect(draft.brand).toEqual({
      logo: "brand-0123456789ab.png",
      line: "Bromma PK",
    });
    rerender(
      <BrandPicker
        name="club"
        draft={draft}
        setDraft={setDraft}
        upload={upload}
        hosted={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove logo" }));
    expect(draft.brand).toEqual({ logo: null, line: "Bromma PK" });
  });

  it("keeps the line only on splitsmith.app and says why", () => {
    render(
      <BrandPicker
        name="club"
        draft={DRAFT}
        setDraft={vi.fn()}
        upload={vi.fn()}
        hosted={true}
      />,
    );
    expect(screen.queryByLabelText("Brand logo file")).toBeNull();
    expect(screen.getByLabelText("Brand line")).toBeTruthy();
    expect(screen.getByText(/desktop app/)).toBeTruthy();
  });

  it("makes the draft dirty, so Save lights up", () => {
    expect(
      isDirty(DRAFT, { ...DRAFT, brand: { logo: null, line: "Bromma PK" } }),
    ).toBe(true);
    expect(isDirty(DRAFT, { ...DRAFT, brand: null })).toBe(false);
  });
});
