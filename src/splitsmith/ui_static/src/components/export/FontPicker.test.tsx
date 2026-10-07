import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FontPicker } from "@/components/export/FontPicker";
import type { FontInfo, StoredLookBody } from "@/lib/api";

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

  it("draws nothing before the catalog lists any face", () => {
    const { container } = render(<FontPicker draft={DRAFT} setDraft={vi.fn()} fonts={[]} />);
    expect(container.textContent).toBe("");
  });
});
