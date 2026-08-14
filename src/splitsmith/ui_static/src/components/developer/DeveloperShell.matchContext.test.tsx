/**
 * Dev mode's match context (?match=) rides the URL. The shell must keep
 * it across its own navigation -- stepper links and tool links -- and a
 * flip back to Match mode should land on that match's home rather than
 * dumping the operator on the picker to choose the same match again.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider, useMode } from "@/lib/mode";
import { RootLayout } from "@/components/layout/RootLayout";
import { DeveloperShell } from "@/components/developer/DeveloperShell";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "u1",
        email: "m@thias.se",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: true, mode: "local" }),
    },
  };
});

vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => false }));

afterEach(() => {
  localStorage.removeItem("splitsmith.mode");
});

function CorpusPage() {
  const { setMode } = useMode();
  return (
    <div>
      corpus page
      <button type="button" onClick={() => setMode("match")}>
        Flip to match
      </button>
    </div>
  );
}

function LocationProbe({ id }: { id: string }) {
  const location = useLocation();
  return <div data-testid={id}>{location.pathname + location.search}</div>;
}

function renderDev(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ModeProvider>
        <AuthProvider>
          <Routes>
            <Route element={<RootLayout />}>
              <Route element={<DeveloperShell />}>
                <Route path="dev/corpus" element={<CorpusPage />} />
              </Route>
              <Route path="match/:matchId" element={<LocationProbe id="match-probe" />} />
              <Route path="pick" element={<LocationProbe id="pick-probe" />} />
              <Route path="*" element={<LocationProbe id="catchall-probe" />} />
            </Route>
          </Routes>
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("DeveloperShell match context", () => {
  it("keeps ?match= on stepper and tool links", async () => {
    renderDev("/dev/corpus?match=m1");
    await screen.findByText("corpus page");
    expect(screen.getByRole("link", { name: /review queue/i })).toHaveAttribute(
      "href",
      "/dev/review?match=m1",
    );
  });

  it("leaves links bare without a match context", async () => {
    renderDev("/dev/corpus");
    await screen.findByText("corpus page");
    expect(screen.getByRole("link", { name: /review queue/i })).toHaveAttribute(
      "href",
      "/dev/review",
    );
  });

  it("flips back to the pinned match's home, not the picker", async () => {
    renderDev("/dev/corpus?match=m1");
    await screen.findByText("corpus page");
    await userEvent.click(screen.getByRole("button", { name: "Flip to match" }));
    await waitFor(() =>
      expect(screen.getByTestId("match-probe")).toHaveTextContent("/match/m1/"),
    );
  });

  it("flips to the root (picker) when no match is pinned", async () => {
    renderDev("/dev/corpus");
    await screen.findByText("corpus page");
    await userEvent.click(screen.getByRole("button", { name: "Flip to match" }));
    await waitFor(() =>
      expect(screen.getByTestId("catchall-probe")).toHaveTextContent("/"),
    );
  });
});
