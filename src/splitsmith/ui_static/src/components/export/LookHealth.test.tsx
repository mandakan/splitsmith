/**
 * The Export rail's Look preflight (#1276): your own Look is checked when
 * chosen, and a template that fails is named before Export, never blocking it.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LookHealth } from "@/components/export/LookHealth";
import { api, type LookInfo } from "@/lib/api";
import { BUILTIN_LOOKS } from "@/lib/looks";
import { lookFailures } from "@/lib/lookHealth";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, checkLook: vi.fn(), outdatedLookTemplates: vi.fn(), refreshLookTemplates: vi.fn() },
  };
});

const club: LookInfo = { ...BUILTIN_LOOKS[0], name: "club", label: "Club", source: "user", editable: true };
const shipped: LookInfo = { ...BUILTIN_LOOKS[0], editable: false };

afterEach(() => vi.clearAllMocks());
beforeEach(() => {
  vi.mocked(api.outdatedLookTemplates).mockResolvedValue({ files: [] });
});

describe("lookFailures", () => {
  it("names each failing card in the UI's words", () => {
    expect(
      lookFailures([
        { subject: "look.json", level: "ok", message: "13 colours" },
        { subject: "title_page rise card-rise.html", level: "error", message: "script error: line 4: boom (sample: x)" },
        { subject: "slate default card.html", level: "warn", message: "runs past the card" },
      ]),
    ).toEqual(["Title page, Rise: script error: line 4: boom (sample: x)"]);
  });

  it("puts the cards one broken file draws on one line", () => {
    const why = "script error: line 28: boom (sample: one shooter with a logo)";
    expect(
      lookFailures([
        { subject: "title_page default card.html", level: "error", message: why },
        { subject: "slate default card.html", level: "error", message: why },
        { subject: "closing default card.html", level: "error", message: why },
      ]),
    ).toEqual([`Title page, Stage slate and Closing card: ${why}`]);
  });
});

describe("LookHealth", () => {
  it("says which card a failing template would leave out", async () => {
    vi.mocked(api.checkLook).mockResolvedValue({
      items: [{ subject: "title_page default card.html", level: "error", message: "script error: line 4: boom" }],
      errors: 1,
      warnings: 0,
    });
    render(<LookHealth look="club" looks={[shipped, club]} hosted={false} />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/left out/);
    expect(alert.textContent).toContain("Title page: script error: line 4: boom");
    expect(api.checkLook).toHaveBeenCalledWith("club", null, []);
  });

  it("shows nothing when the Look checks clean", async () => {
    vi.mocked(api.checkLook).mockResolvedValue({ items: [], errors: 0, warnings: 0 });
    const { container } = render(<LookHealth look="club" looks={[club]} hosted={false} />);
    await waitFor(() => expect(api.checkLook).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("checks neither a shipped Look nor anything on splitsmith.app", () => {
    render(<LookHealth look="splitsmith" looks={[shipped, club]} hosted={false} />);
    render(<LookHealth look="club" looks={[club]} hosted />);
    expect(api.checkLook).not.toHaveBeenCalled();
    expect(api.outdatedLookTemplates).not.toHaveBeenCalled();
  });

  it("says when the Look holds older copies of the shipped cards and switches them to the current ones", async () => {
    vi.mocked(api.checkLook).mockResolvedValue({ items: [], errors: 0, warnings: 0 });
    vi.mocked(api.outdatedLookTemplates).mockResolvedValue({ files: ["card.html", "sting-wipe.html"] });
    vi.mocked(api.refreshLookTemplates).mockResolvedValue({ files: ["card.html", "sting-wipe.html"] });
    const onRefreshed = vi.fn();
    const user = userEvent.setup();
    render(<LookHealth look="club" looks={[club]} hosted={false} onRefreshed={onRefreshed} />);
    const note = await screen.findByRole("status");
    expect(note.textContent).toContain("older copies of the shipped cards");
    expect(note.textContent).toContain("your brand logo");
    await user.click(screen.getByRole("button", { name: "Use the current cards" }));
    expect(api.refreshLookTemplates).toHaveBeenCalledWith("club");
    await waitFor(() => expect(onRefreshed).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });
});
