/**
 * AdminAccess -- the hosted admin's access requests and account tiers.
 * Covers: a non-admin sees no tables; approve sends the picked tier and
 * reloads; a 409 shows the "already decided" line; an approval whose mail
 * failed shows the chip and Resend; a tier change goes through the row
 * menu.
 */
import { render as rtlRender, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AccessRequest, AccessTiers, AdminAccount } from "@/lib/api";

const auth = vi.hoisted(() => ({
  user: { id: "u1", email: "admin@x.se", display_name: null, is_admin: true } as {
    id: string;
    email: string;
    display_name: string | null;
    is_admin: boolean;
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: auth.user }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      adminAccessRequests: vi.fn(),
      adminApproveAccessRequest: vi.fn(),
      adminDeclineAccessRequest: vi.fn(),
      adminResendAccessRequest: vi.fn(),
      adminUsers: vi.fn(),
      adminSetUserTier: vi.fn(),
      adminAccessTiers: vi.fn(),
    },
  };
});

import { ApiError, api } from "@/lib/api";
import { AdminAccess } from "@/pages/AdminAccess";

function req(over: Partial<AccessRequest> = {}): AccessRequest {
  return {
    id: "r1",
    email: "new@x.se",
    note: "Club mate from Bromma",
    source: "login",
    status: "pending",
    requested_at: new Date().toISOString(),
    last_requested_at: new Date().toISOString(),
    decided_at: null,
    decided_by: null,
    tier_granted: null,
    email_sent_at: null,
    ...over,
  };
}

function account(over: Partial<AdminAccount> = {}): AdminAccount {
  return {
    id: "a1",
    email: "user@x.se",
    display_name: "User",
    access_tier: "full",
    created_at: "2026-09-01T10:00:00Z",
    is_admin: false,
    ...over,
  };
}

const tiers: AccessTiers = {
  tiers: [
    { name: "full", features: ["sync", "share", "create_match", "raw_upload", "hosted_compute"] },
    { name: "sharing", features: ["sync", "share"] },
    { name: "disabled", features: [] },
  ],
  default_tier: "full",
};

function render(ui: ReactElement) {
  return rtlRender(<MemoryRouter>{ui}</MemoryRouter>);
}

describe("AdminAccess", () => {
  beforeEach(() => {
    auth.user = { id: "u1", email: "admin@x.se", display_name: null, is_admin: true };
    vi.mocked(api.adminAccessRequests).mockReset().mockResolvedValue([req()]);
    vi.mocked(api.adminUsers).mockReset().mockResolvedValue([account()]);
    vi.mocked(api.adminAccessTiers).mockReset().mockResolvedValue(tiers);
    vi.mocked(api.adminApproveAccessRequest).mockReset();
    vi.mocked(api.adminDeclineAccessRequest).mockReset();
    vi.mocked(api.adminResendAccessRequest).mockReset();
    vi.mocked(api.adminSetUserTier).mockReset();
  });

  it("shows a non-admin no tables and fetches nothing", async () => {
    auth.user = { ...auth.user, is_admin: false };
    render(<AdminAccess />);
    expect(screen.getByText("Admin access required.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(api.adminAccessRequests).not.toHaveBeenCalled();
  });

  it("approves with the sharing tier by default and reloads both lists", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminApproveAccessRequest).mockResolvedValue(
      req({ status: "approved", tier_granted: "sharing", email_sent_at: new Date().toISOString() }),
    );
    render(<AdminAccess />);
    const row = (await screen.findByText("new@x.se")).closest("tr")!;
    expect(within(row).getByRole("button", { name: "sharing" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(row).getByRole("button", { name: "Approve" }));
    expect(api.adminApproveAccessRequest).toHaveBeenCalledWith("r1", "sharing");
    await waitFor(() => expect(api.adminAccessRequests).toHaveBeenCalledTimes(2));
    expect(api.adminUsers).toHaveBeenCalledTimes(2);
  });

  it("keeps a pending row's tier picker on one line (the table scrolls on a phone)", async () => {
    render(<AdminAccess />);
    const row = (await screen.findByText("new@x.se")).closest("tr")!;
    const picker = within(row).getByRole("group", { name: "Tier for new@x.se" });
    expect(picker.className).toMatch(/\bflex-nowrap\b/);
    expect(picker.className).not.toMatch(/\bflex-wrap\b/);
  });

  it("approves with the tier picked on the row", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminApproveAccessRequest).mockResolvedValue(req({ status: "approved" }));
    render(<AdminAccess />);
    const row = (await screen.findByText("new@x.se")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "full" }));
    await user.click(within(row).getByRole("button", { name: "Approve" }));
    expect(api.adminApproveAccessRequest).toHaveBeenCalledWith("r1", "full");
  });

  it("says someone else decided on a 409 and reloads", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminDeclineAccessRequest).mockRejectedValue(
      new ApiError(409, "already decided", "already decided"),
    );
    render(<AdminAccess />);
    const row = (await screen.findByText("new@x.se")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "Decline" }));
    expect(await screen.findByText("Someone else already decided this request.")).toBeInTheDocument();
    await waitFor(() => expect(api.adminAccessRequests).toHaveBeenCalledTimes(2));
  });

  it("shows a 404's detail on the page", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminApproveAccessRequest).mockRejectedValue(
      new ApiError(404, "account deleted", "account deleted"),
    );
    render(<AdminAccess />);
    const row = (await screen.findByText("new@x.se")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("account deleted");
  });

  it("says so when nothing is pending", async () => {
    vi.mocked(api.adminAccessRequests).mockResolvedValue([]);
    render(<AdminAccess />);
    expect(await screen.findByText("No pending requests.")).toBeInTheDocument();
  });

  it("folds decided requests; a failed mail shows the chip and Resend", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminAccessRequests).mockResolvedValue([
      req({
        id: "r9",
        email: "late@x.se",
        status: "approved",
        tier_granted: "sharing",
        decided_by: "admin@x.se",
        decided_at: new Date().toISOString(),
        email_sent_at: null,
      }),
    ]);
    vi.mocked(api.adminResendAccessRequest).mockResolvedValue(req({ id: "r9", status: "approved" }));
    render(<AdminAccess />);
    const toggle = await screen.findByRole("button", { name: /^Decided$/, expanded: false });
    expect(screen.queryByText("late@x.se")).not.toBeInTheDocument();
    await user.click(toggle);
    const row = screen.getByText("late@x.se").closest("tr")!;
    expect(within(row).getByText("Email not sent")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Resend" }));
    expect(api.adminResendAccessRequest).toHaveBeenCalledWith("r9");
    await waitFor(() => expect(api.adminAccessRequests).toHaveBeenCalledTimes(2));
  });

  it("offers no Resend once the mail went out", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminAccessRequests).mockResolvedValue([
      req({ id: "r9", email: "ok@x.se", status: "approved", email_sent_at: new Date().toISOString() }),
    ]);
    render(<AdminAccess />);
    await user.click(await screen.findByRole("button", { name: /^Decided$/, expanded: false }));
    const row = screen.getByText("ok@x.se").closest("tr")!;
    expect(within(row).queryByText("Email not sent")).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Resend" })).not.toBeInTheDocument();
  });

  it("sets an account's tier from its row menu", async () => {
    const user = userEvent.setup();
    vi.mocked(api.adminSetUserTier).mockResolvedValue(account({ access_tier: "disabled" }));
    render(<AdminAccess />);
    await user.click(await screen.findByRole("button", { name: "Set tier for user@x.se" }));
    await user.click(screen.getByRole("menuitem", { name: "Set tier: disabled" }));
    expect(api.adminSetUserTier).toHaveBeenCalledWith("a1", "disabled");
    await waitFor(() => expect(api.adminUsers).toHaveBeenCalledTimes(2));
  });

  it("marks an admin's tier as having no effect", async () => {
    vi.mocked(api.adminUsers).mockResolvedValue([account({ email: "admin@x.se", is_admin: true })]);
    render(<AdminAccess />);
    const row = (await screen.findByText("admin@x.se")).closest("tr")!;
    expect(within(row).getByText("Admin")).toBeInTheDocument();
    expect(within(row).getByText("tier has no effect")).toBeInTheDocument();
  });
});
