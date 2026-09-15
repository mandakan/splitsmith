/**
 * The create-match page opens on the scoreboard variant; manual setup is
 * the fallback the "scoreboard unavailable" notice hands the user to.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CreateMatch } from "@/pages/CreateMatch";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
    },
  };
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/pick/new"]}>
      <CreateMatch />
    </MemoryRouter>,
  );
}

describe("CreateMatch", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ status: 401, ok: false, json: async () => ({}) }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens on the scoreboard variant", () => {
    renderPage();
    expect(screen.getByRole("tab", { name: /from scoreboard/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByPlaceholderText(/search matches/i)).toBeInTheDocument();
  });

  it("falls back to manual setup from the unavailable notice when the search cannot run", async () => {
    renderPage();
    const input = screen.getByPlaceholderText(/search matches/i);
    fireEvent.change(input, { target: { value: "bromma" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    await waitFor(() => expect(screen.getByText("Scoreboard unavailable")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "using manual setup" }));
    expect(screen.getByRole("tab", { name: /manual setup/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByPlaceholderText(/search matches/i)).toBeNull();
  });
});
