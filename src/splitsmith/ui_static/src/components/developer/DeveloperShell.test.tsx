/**
 * DeveloperShell chrome after the RootLayout extraction (#550).
 *
 * Mounted inside a real RootLayout: the assertions are about the seam
 * between the two, so a bare DeveloperShell render would prove nothing.
 * Hosted mode, because AccountChip self-gates and renders nothing local.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
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
        is_admin: true,
      }),
      getServerFeatures: vi
        .fn()
        .mockResolvedValue({ lab: false, mode: "hosted" }),
    },
  };
});

vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => false }));

function renderDev() {
  return render(
    <MemoryRouter initialEntries={["/dev/corpus"]}>
      <ModeProvider>
        <AuthProvider>
          <Routes>
            <Route element={<RootLayout />}>
              <Route element={<DeveloperShell />}>
                <Route path="dev/corpus" element={<div>corpus page</div>} />
              </Route>
            </Route>
          </Routes>
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("DeveloperShell chrome (#550)", () => {
  // Every test waits on the outlet's exact placeholder text rather than a
  // /developer/i or /corpus/i substring match: both substrings are
  // legitimately ambiguous once mounted under a real RootLayout --
  // "Developer" also names the GlobalBar's ModeSwitch option, and
  // "Corpus" also names the sidebar's untouched step-1 nav link. The
  // outlet's literal "corpus page" string is the only unambiguous mount
  // signal.
  it("declares the beep accent", async () => {
    renderDev();
    await screen.findByText("corpus page");
    expect(screen.getByTestId("shell-hairline")).toHaveAttribute(
      "data-accent",
      "beep",
    );
  });

  it("gains the account menu it never had", async () => {
    renderDev();
    expect(await screen.findByTestId("account-chip")).toBeInTheDocument();
  });

  it("keeps the dev breadcrumb and model chip", async () => {
    renderDev();
    await screen.findByText("corpus page");
    // Scoped to <header>: the breadcrumb and ModelChip are the shell's
    // portalled context row, which lives there. Scoping (rather than an
    // unscoped getByText) is what makes "Corpus" unambiguous against the
    // sidebar's own step-1 label of the same name.
    const header = document.querySelector("header");
    expect(header).not.toBeNull();
    expect(within(header!).getByText(/corpus/i)).toBeInTheDocument();
    expect(within(header!).getByText(/active/i)).toBeInTheDocument();
  });

  it("does not render its own mode switch", async () => {
    renderDev();
    await screen.findByText("corpus page");
    // ModeSwitch ships role="radiogroup" aria-label="Mode" -- confirmed
    // against src/components/ui/ModeSwitch.tsx in Task 2. Exactly one:
    // the global bar's. DeveloperShell's own copy is deleted here.
    expect(screen.getAllByRole("radiogroup", { name: /mode/i })).toHaveLength(1);
  });
});
