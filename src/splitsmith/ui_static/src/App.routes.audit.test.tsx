/**
 * Audit route (mobile audit UI, 2026-08-13): `audit/:slug` and
 * `audit/:slug/:stage` branch to MobileAudit below the 768 px breakpoint
 * and keep the gated desktop Audit at or above it, mirroring
 * BeepReviewRoute (App.tsx:63-70).
 *
 * Renders `AuditRoute` directly rather than mounting the full router at
 * a match-scoped URL: that path sits behind AuthGate, MatchShell and
 * ShooterScopedRoute, none of which AuditRoute's own branching touches,
 * so reaching it through the router would mean mocking the whole match
 * shell's API surface (getMe, getServerFeatures, listMatchShooters,
 * jobs, ...) to exercise a two-way branch on useIsMobile. The brief
 * (task-8-brief.md Step 1) sanctions this narrower form when the
 * shell-mocking cost outweighs the value of a full router mount.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { vi } from "vitest";

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => mobile.value }));
vi.mock("@/pages/MobileAudit", () => ({
  MobileAudit: () => <div data-testid="mobile-audit" />,
}));
vi.mock("@/pages/Audit", () => ({ Audit: () => <div data-testid="desktop-audit" /> }));

describe("AuditRoute", () => {
  it("mounts MobileAudit below the breakpoint", async () => {
    mobile.value = true;
    const { AuditRoute } = await import("@/App");
    render(<AuditRoute />);
    expect(await screen.findByTestId("mobile-audit")).toBeInTheDocument();
    expect(screen.queryByTestId("desktop-audit")).not.toBeInTheDocument();
  });

  it("keeps the desktop Audit (still gated) at or above the breakpoint", async () => {
    mobile.value = false;
    const { AuditRoute } = await import("@/App");
    render(<AuditRoute />);
    expect(await screen.findByTestId("desktop-audit")).toBeInTheDocument();
    expect(screen.queryByTestId("mobile-audit")).not.toBeInTheDocument();
  });
});
