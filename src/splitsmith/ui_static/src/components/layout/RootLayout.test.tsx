/**
 * RootLayout (#550).
 *
 * One sticky header for the whole app. These tests pin the three things
 * inner shells depend on: the slot exists and receives portalled markup,
 * the hairline follows the declared accent, and the global bar is absent
 * on mobile (where the nav drawer carries the account menu instead).
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { createPortal } from "react-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import {
  useShellAccent,
  useShellContextSlot,
  useShellOwnsMobileAccount,
} from "@/components/layout/shellChromeContext";
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
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
    },
  };
});

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock("@/lib/useIsMobile", () => ({
  useIsMobile: () => mobile.value,
}));

// jsdom has no ResizeObserver; RootLayout measures the header via
// useShellHeaderHeight, same stub MatchShell.test.tsx uses for the same
// hook.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function OwnsMobile() {
  useShellOwnsMobileAccount();
  return null;
}

/** Stand-in for a real shell: declares an accent and portals a context row. */
function FakeShell({
  accent,
  ownsMobile = false,
}: {
  accent: "led" | "beep";
  ownsMobile?: boolean;
}) {
  useShellAccent(accent);
  const slot = useShellContextSlot();
  return (
    <>
      {ownsMobile ? <OwnsMobile /> : null}
      {slot
        ? createPortal(<div data-testid="ctx-row">breadcrumbs</div>, slot)
        : null}
    </>
  );
}

function renderAt(accent: "led" | "beep" = "led") {
  return render(
    <MemoryRouter initialEntries={["/x"]}>
      <ModeProvider>
        <AuthProvider>
          <Routes>
            <Route element={<RootLayout />}>
              <Route path="x" element={<FakeShell accent={accent} />} />
            </Route>
          </Routes>
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("RootLayout", () => {
  beforeEach(() => {
    mobile.value = false;
    window.ResizeObserver =
      ResizeObserverStub as unknown as typeof window.ResizeObserver;
  });

  it("renders a shell's portalled context row inside the header", async () => {
    renderAt();
    const row = await screen.findByTestId("ctx-row");
    expect(row).toBeInTheDocument();
    expect(row.closest("header")).not.toBeNull();
  });

  it("uses the led hairline by default", async () => {
    renderAt("led");
    await screen.findByTestId("ctx-row");
    expect(screen.getByTestId("shell-hairline")).toHaveAttribute(
      "data-accent",
      "led",
    );
  });

  it("follows a shell that declares the beep accent", async () => {
    renderAt("beep");
    await screen.findByTestId("ctx-row");
    expect(screen.getByTestId("shell-hairline")).toHaveAttribute(
      "data-accent",
      "beep",
    );
  });

  it("renders the global bar on desktop", async () => {
    renderAt();
    await screen.findByTestId("ctx-row");
    expect(
      screen.getByRole("navigation", { name: /global/i }),
    ).toBeInTheDocument();
  });

  it("still renders the global bar on mobile for a shell that has not claimed it", async () => {
    mobile.value = true;
    renderAt();
    await screen.findByTestId("ctx-row");
    expect(
      screen.getByRole("navigation", { name: /global/i }),
    ).toBeInTheDocument();
  });

  it("omits the global bar on mobile when the shell owns the account menu", async () => {
    mobile.value = true;
    render(
      <MemoryRouter initialEntries={["/x"]}>
        <ModeProvider>
          <AuthProvider>
            <Routes>
              <Route element={<RootLayout />}>
                <Route path="x" element={<FakeShell accent="led" ownsMobile />} />
              </Route>
            </Routes>
          </AuthProvider>
        </ModeProvider>
      </MemoryRouter>,
    );
    await screen.findByTestId("ctx-row");
    expect(
      screen.queryByRole("navigation", { name: /global/i }),
    ).not.toBeInTheDocument();
  });
});
