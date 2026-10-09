/**
 * The Branding row under Export, Details (the branding work): where your
 * brand lives (the Look), and the event's own logo, rarely used, as one
 * small optional control.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BrandingField } from "@/components/export/BrandingField";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, uploadEventLogo: vi.fn(), removeEventLogo: vi.fn() } };
});

afterEach(() => vi.clearAllMocks());

describe("BrandingField", () => {
  it("points at the Look for your brand and keeps the event logo optional", () => {
    render(<BrandingField busy={false} />);
    expect(screen.getByText(/Look editor/)).toBeTruthy();
    expect(screen.getByText(/Event logo/)).toBeTruthy();
    expect(screen.getByLabelText("Event logo file")).toBeTruthy();
  });

  it("uploads and removes the event logo, and says why when refused", async () => {
    vi.mocked(api.uploadEventLogo).mockResolvedValue({ event_logo: "event-0123456789ab.png" });
    vi.mocked(api.removeEventLogo).mockResolvedValue({ event_logo: null });
    render(<BrandingField busy={false} />);
    const file = new File([new Uint8Array([137, 80, 78, 71])], "e.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Event logo file"), { target: { files: [file] } });
    await waitFor(() => expect(api.uploadEventLogo).toHaveBeenCalledWith(file));
    expect(await screen.findByRole("button", { name: "Remove event logo" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove event logo" }));
    await waitFor(() => expect(api.removeEventLogo).toHaveBeenCalled());

    vi.mocked(api.uploadEventLogo).mockRejectedValue(new Error("The logo must be a PNG, JPEG or WebP image."));
    fireEvent.change(screen.getByLabelText("Event logo file"), { target: { files: [file] } });
    expect(await screen.findByText(/PNG, JPEG or WebP/)).toBeTruthy();
  });
});
