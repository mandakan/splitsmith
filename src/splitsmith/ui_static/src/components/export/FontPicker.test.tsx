import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FontPicker } from "@/components/export/FontPicker";
import type { FontInfo, OwnFontInfo, StoredLookBody } from "@/lib/api";

const FONTS: FontInfo[] = [
  { id: "antonio", label: "Antonio", role: "display", help: "", url: "/api/looks/fonts/antonio" },
  { id: "bebas-neue", label: "Bebas Neue", role: "display", help: "", url: "/api/looks/fonts/bebas-neue" },
  { id: "jetbrains-mono", label: "JetBrains Mono", role: "mono", help: "", url: "/api/looks/fonts/jetbrains-mono" },
  { id: "ibm-plex-mono", label: "IBM Plex Mono", role: "mono", help: "", url: "/api/looks/fonts/ibm-plex-mono" },
];
const DRAFT: StoredLookBody = { label: "Club", base: "splitsmith", colors: {}, accent_series: [], styles: {} };

describe("FontPicker", () => {
  it("shows the default face as chosen and sets another per role", () => {
    const setDraft = vi.fn();
    render(<FontPicker draft={DRAFT} setDraft={setDraft} fonts={FONTS} />);
    const titles = screen.getByRole("radiogroup", { name: "Titles font" });
    expect(titles.querySelector('[aria-checked="true"]')?.getAttribute("aria-label")).toBe("Antonio");
    fireEvent.click(screen.getByRole("radio", { name: "Bebas Neue" }));
    expect(setDraft).toHaveBeenCalledWith({ ...DRAFT, fonts: { display: "bebas-neue" } });
    fireEvent.click(screen.getByRole("radio", { name: "IBM Plex Mono" }));
    expect(setDraft).toHaveBeenLastCalledWith({ ...DRAFT, fonts: { mono: "ibm-plex-mono" } });
  });

  it("offers the Look's own fonts in both rows and chooses an upload for its row", async () => {
    const mine: OwnFontInfo = { value: "own:font-0123456789ab.ttf", family: "Club Sans", url: "/f/a.ttf" };
    const added: OwnFontInfo = { value: "own:font-ba9876543210.otf", family: "Club Mono", url: "/f/b.otf" };
    const upload = vi.fn().mockResolvedValue(added);
    const setDraft = vi.fn();
    render(<FontPicker draft={DRAFT} setDraft={setDraft} fonts={FONTS} own={{ fonts: [mine], upload }} />);
    const titles = screen.getByRole("radiogroup", { name: "Titles font" });
    const figures = screen.getByRole("radiogroup", { name: "Figures font" });
    expect(titles.querySelector('[aria-label="Club Sans"]')).not.toBeNull();
    expect(figures.querySelector('[aria-label="Club Sans"]')).not.toBeNull();
    fireEvent.click(titles.querySelector('[aria-label="Club Sans"]')!);
    expect(setDraft).toHaveBeenCalledWith({ ...DRAFT, fonts: { display: mine.value } });

    const file = new File([new Uint8Array([0, 1, 0, 0])], "club.otf");
    fireEvent.change(screen.getByLabelText("Add a font file for Figures"), { target: { files: [file] } });
    await waitFor(() => expect(setDraft).toHaveBeenLastCalledWith({ ...DRAFT, fonts: { mono: added.value } }));
    expect(upload).toHaveBeenCalledWith(file);
    expect(screen.getByText(/licensed to use/)).toBeTruthy();
  });

  it("says why an upload was refused and keeps the choice", async () => {
    const upload = vi.fn().mockRejectedValue(new Error("This is a WOFF font. Upload the TTF or OTF file it was made from."));
    const setDraft = vi.fn();
    render(<FontPicker draft={DRAFT} setDraft={setDraft} fonts={FONTS} own={{ fonts: [], upload }} />);
    const file = new File([new Uint8Array([1])], "x.woff2");
    fireEvent.change(screen.getByLabelText("Add a font file for Titles"), { target: { files: [file] } });
    expect(await screen.findByText(/This is a WOFF font/)).toBeTruthy();
    expect(setDraft).not.toHaveBeenCalled();
  });

  it("has no upload where a Look cannot keep a file (hosted)", () => {
    render(<FontPicker draft={DRAFT} setDraft={vi.fn()} fonts={FONTS} />);
    expect(screen.queryByLabelText(/Add a font file/)).toBeNull();
  });

  it("draws nothing before the catalog lists any face", () => {
    const { container } = render(<FontPicker draft={DRAFT} setDraft={vi.fn()} fonts={[]} />);
    expect(container.textContent).toBe("");
  });
});
