/**
 * DeviceLoginDialog state machine (#719).
 *
 * The three transitions that carry real user consequence: approval
 * closes the dialog with the linked account, and the two terminal
 * failures render distinct copy -- "you declined this" and "the code
 * ran out" are different problems and must not share a message.
 *
 * Own file (not folded into HostedAccountChip.test.tsx) because
 * src/lib/features.ts caches the deployment mode per module registry.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DeviceLoginDialog } from "@/components/account/DeviceLoginDialog";
import { ApiError } from "@/lib/api";

const startDeviceLogin = vi.fn();
const getDeviceStatus = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      startDeviceLogin: (...a: unknown[]) => startDeviceLogin(...a),
      getDeviceStatus: (...a: unknown[]) => getDeviceStatus(...a),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
    },
  };
});

const STARTED = {
  user_code: "ABCD-2345",
  verification_uri: "https://hosted.example/desktop/approve",
  verification_uri_complete: "https://hosted.example/desktop/approve?code=ABCD-2345",
  expires_in: 600,
  interval: 1,
};

const ACCOUNT = {
  id: "u1",
  email: "shooter@example.com",
  display_name: null,
  device_name: "gaspode",
  linked_at: "2026-08-08T10:00:00Z",
};

function renderDialog(onLinked = vi.fn()) {
  return {
    onLinked,
    ...render(<DeviceLoginDialog onClose={vi.fn()} onLinked={onLinked} />),
  };
}

describe("DeviceLoginDialog", () => {
  it("shows the user code once the flow starts", async () => {
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({ status: "pending", account: null, device_name: null });
    renderDialog();
    expect(await screen.findByText("ABCD-2345")).toBeInTheDocument();
  });

  it("reports the linked account when the poll approves", async () => {
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({
      status: "approved",
      account: ACCOUNT,
      device_name: "gaspode",
    });
    const { onLinked } = renderDialog();
    await waitFor(() => expect(onLinked).toHaveBeenCalledWith(ACCOUNT));
  });

  it("renders declined copy on denial", async () => {
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({ status: "denied", account: null, device_name: null });
    renderDialog();
    expect(await screen.findByText(/declined/i)).toBeInTheDocument();
    expect(screen.queryByText(/ran out/i)).not.toBeInTheDocument();
  });

  it("renders expiry copy on expiry, distinct from denial", async () => {
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({ status: "expired", account: null, device_name: null });
    renderDialog();
    expect(await screen.findByText(/ran out/i)).toBeInTheDocument();
    expect(screen.queryByText(/declined/i)).not.toBeInTheDocument();
  });

  it("falls through to polling on an already-pending 409, no error banner", async () => {
    // Another call (e.g. a prior dialog mount) already has a login in
    // flight on this install. The local server never echoes the secret
    // device_code back to us, so there is no user_code to show here --
    // but this is explicitly not an error state (behaviour 2 in the
    // task brief): no role="alert" banner, and polling still runs.
    startDeviceLogin.mockRejectedValue(
      new ApiError(409, "device_login_already_pending", "device_login_already_pending"),
    );
    getDeviceStatus.mockResolvedValue({ status: "pending", account: null, device_name: null });
    renderDialog();
    expect(await screen.findByText(/already in progress/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await waitFor(() => expect(getDeviceStatus).toHaveBeenCalled());
  });

  it("keeps polling through a transient 502 instead of treating it as terminal", async () => {
    // Behaviour 3 in the task brief: a hosted-side network failure makes
    // the status poll 502, and the server deliberately leaves the flow
    // alive. A poll that throws must not flip the dialog into an error
    // or terminal phase -- the next tick can still approve.
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus
      .mockRejectedValueOnce(new ApiError(502, "could not reach the hosted server: timeout"))
      .mockResolvedValue({ status: "approved", account: ACCOUNT, device_name: "gaspode" });
    const { onLinked } = renderDialog();
    expect(await screen.findByText("ABCD-2345")).toBeInTheDocument();
    // The failing poll consumes the first tick; the approval only lands
    // on the interval tick after that (STARTED.interval is 1s), so the
    // default 1s waitFor timeout is a coin flip here -- give it room.
    await waitFor(() => expect(onLinked).toHaveBeenCalledWith(ACCOUNT), { timeout: 3000 });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
