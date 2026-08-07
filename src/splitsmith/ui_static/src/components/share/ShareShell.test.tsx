import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ShareShell } from "@/components/share/ShareShell";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn(),
      getProject: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";

function renderShare() {
  return render(
    <MemoryRouter initialEntries={["/share/tok123/results"]}>
      <Routes>
        <Route path="share/:token" element={<ShareShell />}>
          <Route path="results" element={<div>SHARE CONTENT</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("ShareShell branding chrome", () => {
  it("renders branded header + footer around live share content", async () => {
    // Empty roster: pickDefaultShooterSlug finds no slug, so no project
    // fetch fires and the outlet renders directly.
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/x",
      match_name: "m",
      shooters: [],
      origin: null,
    } as never);
    renderShare();
    expect(await screen.findByText("SHARE CONTENT")).toBeInTheDocument();
    const brand = screen.getByRole("link", { name: /splitsmith$/i });
    expect(brand).toHaveAttribute("href", "https://splitsmith.app");
    expect(brand).toHaveAttribute("target", "_blank");
    const footer = screen.getByRole("link", {
      name: /made with splitsmith - analyze your own matches/i,
    });
    expect(footer).toHaveAttribute("href", "https://splitsmith.app");
  });

  it("keeps the header on the dead-link page", async () => {
    vi.mocked(api.listMatchShooters).mockRejectedValue(new Error("404"));
    renderShare();
    expect(
      await screen.findByText("This link is no longer available"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /splitsmith$/i })).toHaveAttribute(
      "href",
      "https://splitsmith.app",
    );
  });
});
