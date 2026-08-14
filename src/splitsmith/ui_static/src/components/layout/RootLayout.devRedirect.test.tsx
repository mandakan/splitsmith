/**
 * RootLayout's shell-less dev-mode redirect.
 *
 * The redirect exists so flipping the mode switch to Developer while on
 * a shell-less route (/pick etc.) lands on /dev/corpus. It must NOT
 * fire on first paint from a persisted mode: an unbound ``--lab``
 * launch with ``splitsmith.mode: developer`` left in localStorage used
 * to bounce straight off /pick, so the operator could never see the
 * match picker (and dev mode has no picker of its own).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider, useMode } from "@/lib/mode";
import { RootLayout } from "@/components/layout/RootLayout";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "local",
        email: "local@splitsmith",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: true, mode: "local" }),
    },
  };
});

vi.mock("@/lib/useIsMobile", () => ({
  useIsMobile: () => false,
}));

afterEach(() => {
  localStorage.removeItem("splitsmith.mode");
});

function FakePick() {
  const { setMode } = useMode();
  return (
    <div data-testid="pick-page">
      <button type="button" onClick={() => setMode("developer")}>
        Developer
      </button>
    </div>
  );
}

function renderAtPick() {
  return render(
    <MemoryRouter initialEntries={["/pick"]}>
      <ModeProvider>
        <AuthProvider>
          <Routes>
            <Route element={<RootLayout />}>
              <Route path="pick" element={<FakePick />} />
              <Route path="dev/corpus" element={<div data-testid="dev-corpus" />} />
            </Route>
          </Routes>
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("RootLayout shell-less dev redirect", () => {
  it("keeps /pick on screen when developer mode is merely persisted", async () => {
    localStorage.setItem("splitsmith.mode", "developer");
    renderAtPick();
    expect(await screen.findByTestId("pick-page")).toBeInTheDocument();
    expect(screen.queryByTestId("dev-corpus")).not.toBeInTheDocument();
  });

  it("still redirects to /dev/corpus on a real flip to developer", async () => {
    localStorage.setItem("splitsmith.mode", "match");
    renderAtPick();
    await screen.findByTestId("pick-page");
    await userEvent.click(screen.getByRole("button", { name: "Developer" }));
    await waitFor(() =>
      expect(screen.getByTestId("dev-corpus")).toBeInTheDocument(),
    );
  });
});
