/**
 * The create-match page opens on the scoreboard variant; manual setup is
 * the fallback the "scoreboard unavailable" notice hands the user to.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { CreateMatch } from "@/pages/CreateMatch";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
      createMatchManual: vi.fn(),
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

  it("shows an access refusal from create as one line, not the raw detail", async () => {
    vi.mocked(api.createMatchManual).mockRejectedValue(
      new ApiError(403, JSON.stringify({ code: "feature_required", feature: "create_match" }), {
        code: "feature_required",
        feature: "create_match",
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /manual setup/i }));
    fireEvent.change(screen.getByPlaceholderText(/draw drills/i), { target: { value: "Club night" } });
    fireEvent.change(screen.getByPlaceholderText("~/Splitsmith/<slug>/"), { target: { value: "/m/club" } });
    fireEvent.change(screen.getByPlaceholderText("Full name"), { target: { value: "Anna" } });
    fireEvent.click(screen.getByRole("button", { name: /create match/i }));
    expect(
      await screen.findByText("Creating matches here is not included in this account's access."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/feature_required/)).toBeNull();
  });
});
