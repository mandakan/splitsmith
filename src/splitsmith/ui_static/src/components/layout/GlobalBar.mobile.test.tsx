/**
 * GlobalBar -- phone-width treatment (#733).
 *
 * On /pick and the other shell-less routes RootLayout renders GlobalBar on
 * a phone (nothing else owns the account menu there), and at 390px the bar
 * was 656px wide signed in: the account chip started at x=326 and ran off
 * the right edge, taking the linked email and the sign-out control with
 * it. Measured in a real browser -- jsdom has no layout engine, so these
 * tests assert the content decisions that free the pixels, not the
 * geometry. The measurement that proves the fix lives in the PR.
 *
 * Local mode here, so HostedAccountChip is the chip on screen (AccountChip
 * self-gates to null). The hosted half is AccountChip.mobile.test.tsx --
 * a separate file because src/lib/features.ts caches getServerFeatures()
 * in a module-level promise that is never invalidated, so one file cannot
 * observe both modes. Same reason GlobalBar.hosted.test.tsx is separate.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import { GlobalBar } from "@/components/layout/GlobalBar";

const isMobile = vi.hoisted(() => ({ value: true }));

vi.mock("@/lib/useIsMobile", () => ({
  useIsMobile: () => isMobile.value,
}));

const account = vi.hoisted(() => ({
  value: {
    email: "mathias.axell@example.com",
    display_name: null,
    device_name: "gaspode-desktop",
  } as { email: string; display_name: string | null; device_name: string } | null,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "local",
        email: "local@localhost",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
      getSyncSettings: vi.fn(() => Promise.resolve({ account: account.value })),
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

afterEach(() => {
  isMobile.value = true;
  account.value = {
    email: "mathias.axell@example.com",
    display_name: null,
    device_name: "gaspode-desktop",
  };
});

describe("GlobalBar at phone width (#733)", () => {
  it("drops the wordmark, which the brand glyph beside it already says", async () => {
    renderBar();
    await screen.findByTestId("hosted-account-chip");
    expect(screen.queryByText("Splitsmith")).not.toBeInTheDocument();
  });

  it("keeps the wordmark on a desktop viewport", async () => {
    isMobile.value = false;
    renderBar();
    await screen.findByTestId("hosted-account-chip");
    expect(screen.getByText("Splitsmith")).toBeInTheDocument();
  });

  it("keeps the linked email -- it is what says which account this is", async () => {
    renderBar();
    expect(
      await screen.findByText("mathias.axell@example.com"),
    ).toBeInTheDocument();
  });

  it("drops the device name, which the email already implies on one device", async () => {
    renderBar();
    await screen.findByText("mathias.axell@example.com");
    expect(screen.queryByText(/gaspode-desktop/)).not.toBeInTheDocument();
  });

  it("keeps the device name on a desktop viewport", async () => {
    isMobile.value = false;
    renderBar();
    await screen.findByText("mathias.axell@example.com");
    expect(screen.getByText(/gaspode-desktop/)).toBeInTheDocument();
  });

  it("keeps sign-out reachable rather than pushing it off the edge", async () => {
    renderBar();
    await screen.findByText("mathias.axell@example.com");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("shortens the sign-in label, which is 155px of a 390px bar", async () => {
    account.value = null;
    renderBar();
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Sign in to splitsmith.app" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the full sign-in label on a desktop viewport", async () => {
    account.value = null;
    isMobile.value = false;
    renderBar();
    expect(
      await screen.findByRole("button", { name: "Sign in to splitsmith.app" }),
    ).toBeInTheDocument();
  });
});
