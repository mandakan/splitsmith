/**
 * The account pill (spec 2026-10-09): everything about you behind your
 * avatar, on every page, in both modes.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountMenu } from "@/components/account/AccountMenu";
import { api } from "@/lib/api";

let mockMode: "local" | "hosted" = "local";
let mockStatus = "authed";

vi.mock("@/lib/features", () => ({ useDeploymentMode: () => ({ mode: mockMode, resolved: true }) }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ status: mockStatus }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, getScoreboardIdentity: vi.fn(), getShooterBook: vi.fn() } };
});

beforeEach(() => {
  mockMode = "local";
  mockStatus = "authed";
  vi.mocked(api.getScoreboardIdentity).mockResolvedValue({
    shooter_id: 42,
    display_name: "Mathias Axell",
    division: null,
    club: null,
    base_url: null,
  });
  vi.mocked(api.getShooterBook).mockResolvedValue({
    entries: [
      { shooter_id: 42, label: "Mathias Axell", identity: { accent: null, logo: "logo-0123456789ab.png", club: null }, updated_at: "" },
    ],
  });
});

function renderMenu() {
  return render(
    <MemoryRouter>
      <AccountMenu />
    </MemoryRouter>,
  );
}

describe("AccountMenu", () => {
  it("shows your logo and opens You, Shooters and Branding locally", async () => {
    const { container } = renderMenu();
    await waitFor(() =>
      expect(container.querySelector("img")?.getAttribute("src")).toBe(
        "/api/me/shooter-book/42/logo?v=logo-0123456789ab.png",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Your account" }));
    const items = await screen.findAllByRole("menuitem");
    expect(items.map((i) => [i.textContent, i.getAttribute("href")])).toEqual([
      ["You", "/you"],
      ["Shooters", "/shooters"],
      ["Branding", "/you#brand"],
    ]);
  });

  it("adds Account on splitsmith.app and hides until you are signed in", async () => {
    mockMode = "hosted";
    const { unmount } = renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "Your account" }));
    expect((await screen.findAllByRole("menuitem")).map((i) => i.textContent)).toContain("Account");
    unmount();
    mockStatus = "anon";
    renderMenu();
    expect(screen.queryByRole("button", { name: "Your account" })).toBeNull();
  });
});
