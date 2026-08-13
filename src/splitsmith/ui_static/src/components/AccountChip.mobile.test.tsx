/**
 * AccountChip -- phone-width treatment (#733).
 *
 * #733 measured HostedAccountChip and noted AccountChip "likely has the
 * same shape, but it is not new and was not measured here". It was
 * measured for this fix, in a real browser at 390px: the admin variant ran
 * 326 -> 632, i.e. 242px past the right edge, so it overflows harder than
 * the chip the issue was filed about.
 *
 * The treatment differs from HostedAccountChip's on purpose. That chip
 * keeps its email on a phone because the email is its whole point -- it
 * says which hosted account this desktop install is linked to. Here you
 * *are* the account, so the email is the one thing on the chip that can go
 * without losing a fact or an affordance; all three controls stay. With
 * three icon buttons the admin variant needs 130px of the 158px a 390px
 * bar has left, which the email would not fit into legibly anyway.
 *
 * Hosted mode, in its own file: src/lib/features.ts caches
 * getServerFeatures() in a module-level promise that is never
 * invalidated, so one file cannot observe both deployment modes. See
 * GlobalBar.hosted.test.tsx for the same constraint.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountChip } from "@/components/AccountChip";
import { AuthProvider } from "@/lib/auth";

const isMobile = vi.hoisted(() => ({ value: true }));

vi.mock("@/lib/useIsMobile", () => ({
  useIsMobile: () => isMobile.value,
}));

const admin = vi.hoisted(() => ({ value: true }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn(() =>
        Promise.resolve({
          id: "u1",
          email: "mathias.axell@example.com",
          display_name: null,
          is_admin: admin.value,
        }),
      ),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "hosted" }),
    },
  };
});

function renderChip() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <AccountChip />
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  isMobile.value = true;
  admin.value = true;
});

describe("AccountChip at phone width (#733)", () => {
  it("drops the email -- in hosted mode you are the account", async () => {
    renderChip();
    await screen.findByTestId("account-chip");
    expect(
      screen.queryByText("mathias.axell@example.com"),
    ).not.toBeInTheDocument();
  });

  it("keeps the email on a desktop viewport", async () => {
    isMobile.value = false;
    renderChip();
    expect(
      await screen.findByText("mathias.axell@example.com"),
    ).toBeInTheDocument();
  });

  it("keeps every control reachable instead of off the right edge", async () => {
    renderChip();
    await screen.findByTestId("account-chip");
    expect(screen.getByRole("link", { name: "Workers (admin)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Account" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("keeps the non-admin controls too", async () => {
    admin.value = false;
    renderChip();
    await screen.findByTestId("account-chip");
    expect(screen.queryByRole("link", { name: "Workers (admin)" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Account" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("links to the account page", async () => {
    // Uses the same AuthProvider + MemoryRouter wrapper as the other
    // tests here (useAuth() throws outside AuthProvider); the brief's
    // wrapper snippet is for a file with no auth context to satisfy.
    renderChip();
    await screen.findByTestId("account-chip");
    expect(screen.getByRole("link", { name: "Account" })).toHaveAttribute(
      "href",
      "/account",
    );
  });
});
