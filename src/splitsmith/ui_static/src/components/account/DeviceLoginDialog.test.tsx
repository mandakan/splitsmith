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
import { act, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DeviceLoginDialog } from "@/components/account/DeviceLoginDialog";
import { ApiError, type HostedAccountInfo } from "@/lib/api";

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
  resumed: false,
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

  it("shows the code and the approve link when the login is resumed", async () => {
    // A login was already in flight on this install (the operator
    // cancelled the dialog and signed in again, or reloaded mid-flow).
    // ``start`` hands that flow back with resumed: true rather than
    // refusing -- and the whole point of the fix is that this state is
    // NOT a dead end: the code and the approve button must both be here,
    // with no error banner, or the operator has to wait out the TTL.
    startDeviceLogin.mockResolvedValue({ ...STARTED, resumed: true, expires_in: 412 });
    getDeviceStatus.mockResolvedValue({ status: "pending", account: null, device_name: null });
    renderDialog();

    expect(await screen.findByText("ABCD-2345")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /open splitsmith\.app to approve/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/already in progress on this install/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await waitFor(() => expect(getDeviceStatus).toHaveBeenCalled());
  });

  it("does not claim a resume when the login is a fresh one", async () => {
    // The inverse of the test above -- without it, the resume note could
    // render unconditionally and nothing would notice.
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({ status: "pending", account: null, device_name: null });
    renderDialog();

    expect(await screen.findByText("ABCD-2345")).toBeInTheDocument();
    expect(screen.queryByText(/already in progress on this install/i)).not.toBeInTheDocument();
  });

  it("treats an idle status while waiting as terminal, not as keep-polling", async () => {
    // The local server lost the flow (it restarted mid-login, so the
    // device_code is gone with it). Nothing can ever change from here,
    // so the dialog must stop and offer a fresh start -- it used to sit
    // on "Waiting for approval..." until the operator gave up.
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({ status: "idle", account: null, device_name: null });
    renderDialog();

    expect(await screen.findByText(/no longer in progress/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(screen.queryByText(/waiting for approval/i)).not.toBeInTheDocument();

    // And it really stopped: no further polls after the terminal verdict.
    const callsAtTerminal = getDeviceStatus.mock.calls.length;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1200));
    });
    expect(getDeviceStatus.mock.calls.length).toBe(callsAtTerminal);
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

  /**
   * HostedAccountChip -- the dialog's only caller -- passes fresh inline
   * `onClose`/`onLinked` arrows on every render, and it mounts inside
   * MatchShell, which re-renders on useJobs()'s 1-5s poll cadence
   * regardless of whether anything the dialog cares about changed. This
   * component mimics that: a parent that re-renders on its own fast timer
   * with brand-new inline callbacks each time, unrelated to the dialog's
   * own phase/started state.
   */
  function ChurnyParent({
    onLinked,
    renderIntervalMs,
  }: {
    onLinked: (account: HostedAccountInfo) => void;
    renderIntervalMs: number;
  }) {
    const [, setTick] = useState(0);
    useEffect(() => {
      const id = setInterval(() => setTick((t) => t + 1), renderIntervalMs);
      return () => clearInterval(id);
    }, [renderIntervalMs]);
    // New arrow identity every render, same as HostedAccountChip's
    // `onClose={() => setLoginOpen(false)}` / `onLinked={(linked) => {...}}`.
    return (
      <DeviceLoginDialog onClose={() => {}} onLinked={(account) => onLinked(account)} />
    );
  }

  it("polls at the configured interval, not at the parent's re-render rate", async () => {
    // A long poll interval (5s) so the real setInterval tick has no
    // chance to fire in this test's window -- any call to getDeviceStatus
    // beyond the single immediate one on mount has to have come from the
    // poll effect re-running because its dependencies changed, i.e. churn.
    startDeviceLogin.mockResolvedValue({ ...STARTED, interval: 5 });
    getDeviceStatus.mockResolvedValue({ status: "pending", account: null, device_name: null });
    const onLinked = vi.fn();

    render(<ChurnyParent onLinked={onLinked} renderIntervalMs={20} />);
    await screen.findByText("ABCD-2345");
    getDeviceStatus.mockClear();

    // Let the parent's 20ms timer force roughly ten re-renders, each with
    // fresh onClose/onLinked identities, well inside the 5s poll interval.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 220));
    });

    // A stable poll effect makes no more than the (already-cleared) initial
    // call in this window; an effect that depends on the inline callbacks
    // tears down and rebuilds on every parent re-render, firing its own
    // "poll once immediately" tick each time -- so a churning effect would
    // show up here as several extra calls, not zero or one.
    expect(getDeviceStatus.mock.calls.length).toBeLessThanOrEqual(1);
  });
});
