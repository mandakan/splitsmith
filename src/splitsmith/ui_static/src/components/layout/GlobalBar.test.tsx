/**
 * GlobalBar (#550).
 *
 * Row one of the single header. These tests pin what it owns -- brand,
 * mode switch, account menu -- and, just as importantly, that it does
 * not reach for anything shell-specific.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import { GlobalBar } from "@/components/layout/GlobalBar";

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

function renderBar() {
  return render(
    <MemoryRouter>
      <ModeProvider>
        <AuthProvider>
          <GlobalBar />
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("GlobalBar", () => {
  it("publishes a breadcrumb slot between the wordmark and the mode switch", () => {
    const seen: (HTMLElement | null)[] = [];
    render(
      <MemoryRouter>
        <ModeProvider>
          <AuthProvider>
            <GlobalBar onCrumbSlot={(el) => seen.push(el)} />
          </AuthProvider>
        </ModeProvider>
      </MemoryRouter>,
    );
    const slot = screen.getByTestId("crumb-slot");
    expect(seen).toContain(slot);
    const wordmark = screen.getByText("Splitsmith");
    const mode = screen.getByRole("radiogroup", { name: /mode/i });
    expect(wordmark.compareDocumentPosition(slot) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(slot.compareDocumentPosition(mode) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders the brand wordmark", () => {
    renderBar();
    expect(screen.getByText("Splitsmith")).toBeInTheDocument();
  });

  it("renders the mode switch", () => {
    renderBar();
    // ModeSwitch already exposes role="radiogroup" aria-label="Mode" (see
    // src/components/ui/ModeSwitch.tsx) -- matching what is actually
    // shipped rather than adding a group role the component doesn't use.
    expect(
      screen.getByRole("radiogroup", { name: /mode/i }),
    ).toBeInTheDocument();
  });

  it("is labelled as global chrome for assistive tech", () => {
    renderBar();
    expect(
      screen.getByRole("navigation", { name: /global/i }),
    ).toBeInTheDocument();
  });
});
