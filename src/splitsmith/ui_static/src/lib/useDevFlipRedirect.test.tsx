/**
 * useDevFlipRedirect: navigate to /dev/corpus when the operator flips
 * the global mode switch to Developer -- and ONLY on a real flip. A
 * persisted developer mode must not bounce a freshly-mounted route
 * (that's what made /pick, /review and /promote-review unreachable
 * whenever ``splitsmith.mode: developer`` was left in localStorage).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { ModeProvider, useMode } from "@/lib/mode";
import { useDevFlipRedirect } from "@/lib/useDevFlipRedirect";

afterEach(() => {
  localStorage.removeItem("splitsmith.mode");
});

function Host({ enabled = true }: { enabled?: boolean }) {
  const { setMode } = useMode();
  useDevFlipRedirect(enabled);
  return (
    <div data-testid="host">
      <button type="button" onClick={() => setMode("developer")}>
        Developer
      </button>
    </div>
  );
}

function renderHost(enabled?: boolean) {
  return render(
    <MemoryRouter initialEntries={["/x"]}>
      <ModeProvider>
        <Routes>
          <Route path="x" element={<Host enabled={enabled} />} />
          <Route path="dev/corpus" element={<div data-testid="dev-corpus" />} />
        </Routes>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("useDevFlipRedirect", () => {
  it("does not redirect when developer mode is merely persisted", () => {
    localStorage.setItem("splitsmith.mode", "developer");
    renderHost();
    expect(screen.getByTestId("host")).toBeInTheDocument();
    expect(screen.queryByTestId("dev-corpus")).not.toBeInTheDocument();
  });

  it("redirects to /dev/corpus on a real flip to developer", async () => {
    localStorage.setItem("splitsmith.mode", "match");
    renderHost();
    await userEvent.click(screen.getByRole("button", { name: "Developer" }));
    await waitFor(() =>
      expect(screen.getByTestId("dev-corpus")).toBeInTheDocument(),
    );
  });

  it("stays put on a flip while disabled", async () => {
    localStorage.setItem("splitsmith.mode", "match");
    renderHost(false);
    await userEvent.click(screen.getByRole("button", { name: "Developer" }));
    expect(screen.getByTestId("host")).toBeInTheDocument();
    expect(screen.queryByTestId("dev-corpus")).not.toBeInTheDocument();
  });
});
