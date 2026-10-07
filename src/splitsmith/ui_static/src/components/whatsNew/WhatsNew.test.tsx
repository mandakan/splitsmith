/**
 * What's new: the sheet opens itself once when something is unseen and
 * closing it marks what it listed; the bar's button reopens the history;
 * a feature's "New" chip goes when the feature is used; a failed fetch
 * shows nothing.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NewChip, WhatsNewButton, WhatsNewSheet } from "@/components/whatsNew/WhatsNew";
import { api, type WhatsNewPayload } from "@/lib/api";
import { dismissNewChip, resetWhatsNewForTests } from "@/lib/useWhatsNew";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, getWhatsNew: vi.fn(), markWhatsNewSeen: vi.fn() } };
});

const today = new Date().toISOString().slice(0, 10);
const PAYLOAD: WhatsNewPayload = {
  entries: [
    { id: "look-editor", date: today, title: "Make your own Look", body: "Duplicate a Look.", chip: "look-editor" },
    { id: "older", date: "2026-09-01", title: "An older change", body: "It shipped.", chip: null },
  ],
  seen: ["older"],
};

function mountAll() {
  return render(
    <>
      <WhatsNewButton />
      <NewChip feature="look-editor" />
      <WhatsNewSheet />
    </>,
  );
}

beforeEach(() => {
  resetWhatsNewForTests();
  vi.mocked(api.markWhatsNewSeen).mockImplementation(async (ids) => ({
    ...PAYLOAD,
    seen: [...PAYLOAD.seen, ...ids],
  }));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("What's new", () => {
  it("opens once on unseen entries, lists only those, and marks them on close", async () => {
    vi.mocked(api.getWhatsNew).mockResolvedValue(PAYLOAD);
    mountAll();
    const dialog = await screen.findByRole("dialog", { name: "What's new" });
    expect(dialog.textContent).toContain("Make your own Look");
    expect(dialog.textContent).not.toContain("An older change");
    expect(screen.getByRole("button", { name: "What's new, 1 unseen" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    await waitFor(() => expect(api.markWhatsNewSeen).toHaveBeenCalledWith(["look-editor"]));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await screen.findByRole("button", { name: "What's new" })).toBeTruthy();
  });

  it("stays closed with nothing new, and the button shows the history", async () => {
    vi.mocked(api.getWhatsNew).mockResolvedValue({ ...PAYLOAD, seen: ["look-editor", "older"] });
    mountAll();
    const button = await screen.findByRole("button", { name: "What's new" });
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(button);
    const dialog = await screen.findByRole("dialog", { name: "What's new" });
    expect(dialog.textContent).toContain("An older change");
  });

  it("shows the feature's chip until the feature is used", async () => {
    vi.mocked(api.getWhatsNew).mockResolvedValue({ ...PAYLOAD, seen: ["look-editor", "older"] });
    mountAll();
    expect(await screen.findByText("New")).toBeTruthy();
    await act(async () => {
      dismissNewChip("look-editor");
    });
    expect(api.markWhatsNewSeen).toHaveBeenCalledWith(["chip:look-editor"]);
    await waitFor(() => expect(screen.queryByText("New")).toBeNull());
  });

  it("shows nothing when the feed cannot be read", async () => {
    vi.mocked(api.getWhatsNew).mockRejectedValue(new Error("401"));
    const { container } = mountAll();
    await waitFor(() => expect(api.getWhatsNew).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
