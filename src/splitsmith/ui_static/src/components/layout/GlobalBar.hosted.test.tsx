/**
 * GlobalBar -- hosted-mode account menu (#550 review finding).
 *
 * `GlobalBar.test.tsx` mocks `getServerFeatures` to `mode: "local"` for
 * all of its tests, under which `AccountChip` self-gates to null (see
 * `src/components/AccountChip.tsx:31`). That left the account-menu third
 * of GlobalBar's responsibility completely uncovered.
 *
 * This is a separate file rather than a new describe block in
 * `GlobalBar.test.tsx` on purpose: `src/lib/features.ts` caches the
 * `getServerFeatures()` result in a module-level promise (`let cached`)
 * that is never invalidated, so once any test in a file resolves it to
 * "local" every later test in that same file -- regardless of a
 * per-test mock override -- keeps observing "local" too. A fresh test
 * file gets a fresh module registry (and therefore a fresh, unpopulated
 * cache), which sidesteps the problem without touching `features.ts`.
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
        id: "u1",
        email: "shooter@example.com",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "hosted" }),
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

describe("GlobalBar (hosted mode)", () => {
  it("renders the account menu once the account resolves", async () => {
    renderBar();
    expect(
      await screen.findByText("shooter@example.com"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /sign out/i }),
    ).toBeInTheDocument();
  });
});
