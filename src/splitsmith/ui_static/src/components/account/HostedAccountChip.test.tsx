/**
 * HostedAccountChip (#719) -- the local install's linked-account chip.
 *
 * Local mode throughout this file; the hosted-mode self-gate lives in
 * HostedAccountChip.hosted.test.tsx because src/lib/features.ts caches
 * the deployment mode in a module-level promise with no invalidation,
 * so the first mode resolved in a file wins for the whole file.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HostedAccountChip } from "@/components/account/HostedAccountChip";
import { HOSTED_ACCOUNT_CHANGED_EVENT } from "@/lib/api";

const getSyncSettings = vi.fn();
const unlinkHostedAccount = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSyncSettings: (...a: unknown[]) => getSyncSettings(...a),
      unlinkHostedAccount: (...a: unknown[]) => unlinkHostedAccount(...a),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
    },
  };
});

const ACCOUNT = {
  id: "u1",
  email: "shooter@example.com",
  display_name: null,
  device_name: "gaspode",
  linked_at: "2026-08-08T10:00:00Z",
};

describe("HostedAccountChip (local mode)", () => {
  beforeEach(() => {
    getSyncSettings.mockReset();
    unlinkHostedAccount.mockReset();
  });

  it("offers sign-in when nothing is linked", async () => {
    getSyncSettings.mockResolvedValue({
      base_url: "https://hosted.example",
      token_set: false,
      account: null,
    });
    render(<HostedAccountChip />);
    expect(
      await screen.findByRole("button", { name: /sign in to splitsmith\.app/i }),
    ).toBeInTheDocument();
  });

  it("shows the linked email and device once linked", async () => {
    getSyncSettings.mockResolvedValue({
      base_url: "https://hosted.example",
      token_set: true,
      account: ACCOUNT,
    });
    render(<HostedAccountChip />);
    expect(await screen.findByText("shooter@example.com")).toBeInTheDocument();
    expect(screen.getByText(/gaspode/)).toBeInTheDocument();
  });

  it("signs out and returns to the signed-out label", async () => {
    getSyncSettings.mockResolvedValue({
      base_url: "https://hosted.example",
      token_set: true,
      account: ACCOUNT,
    });
    unlinkHostedAccount.mockResolvedValue({ cleared: true, hosted_revoked: true });
    render(<HostedAccountChip />);
    await userEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(unlinkHostedAccount).toHaveBeenCalled());
    expect(
      await screen.findByRole("button", { name: /sign in to splitsmith\.app/i }),
    ).toBeInTheDocument();
  });

  it("says so when the hosted revoke could not be confirmed", async () => {
    // The local copy is gone either way; the operator needs to be told to
    // check the account page. Asserted on rendered text, not on a prop --
    // on #617 a note reached the cell and got ellipsized away while the
    // assertion still passed.
    getSyncSettings.mockResolvedValue({
      base_url: "https://hosted.example",
      token_set: true,
      account: ACCOUNT,
    });
    unlinkHostedAccount.mockResolvedValue({ cleared: true, hosted_revoked: false });
    render(<HostedAccountChip />);
    await userEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    expect(await screen.findByText(/account page/i)).toBeInTheDocument();
  });

  it("refetches when the hosted account changes elsewhere (#736)", async () => {
    getSyncSettings.mockResolvedValueOnce({
      base_url: "https://hosted.example",
      token_set: false,
      account: null,
    });
    render(<HostedAccountChip />);
    expect(
      await screen.findByRole("button", { name: /sign in to splitsmith\.app/i }),
    ).toBeInTheDocument();

    getSyncSettings.mockResolvedValueOnce({
      base_url: "https://hosted.example",
      token_set: true,
      account: ACCOUNT,
    });
    act(() => {
      window.dispatchEvent(new CustomEvent(HOSTED_ACCOUNT_CHANGED_EVENT));
    });
    expect(await screen.findByText("shooter@example.com")).toBeInTheDocument();
  });
});
