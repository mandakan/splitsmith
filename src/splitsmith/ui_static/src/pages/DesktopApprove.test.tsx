/**
 * The approval screen (#719).
 *
 * Hosted-mode surface: this is where the operator's browser turns a
 * pending device authorization into an approval. Covers the prefilled
 * path, both decisions, the different-browser fallback (manual code
 * entry), and the uniform not-found message.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { DesktopApprove } from "@/pages/DesktopApprove";

const getDevicePending = vi.fn();
const approveDevice = vi.fn();
const denyDevice = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getDevicePending: (...a: unknown[]) => getDevicePending(...a),
      approveDevice: (...a: unknown[]) => approveDevice(...a),
      denyDevice: (...a: unknown[]) => denyDevice(...a),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "hosted" }),
    },
  };
});

const PENDING = {
  user_code: "ABCD-2345",
  device_name: "gaspode",
  scope: "sync",
  created_at: "2026-08-08T10:00:00Z",
  expires_at: "2026-08-08T10:10:00Z",
};

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/desktop/approve${search}`]}>
      <Routes>
        <Route path="/desktop/approve" element={<DesktopApprove />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DesktopApprove", () => {
  beforeEach(() => {
    getDevicePending.mockReset();
    approveDevice.mockReset();
    denyDevice.mockReset();
  });

  it("shows the pending device and both decisions", async () => {
    getDevicePending.mockResolvedValue(PENDING);
    renderAt("?code=ABCD-2345");
    expect(await screen.findByText(/gaspode/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /deny/i })).toBeInTheDocument();
  });

  it("approves and confirms", async () => {
    getDevicePending.mockResolvedValue(PENDING);
    approveDevice.mockResolvedValue({ approved: true });
    renderAt("?code=ABCD-2345");
    await userEvent.click(await screen.findByRole("button", { name: /approve/i }));
    expect(approveDevice).toHaveBeenCalledWith("ABCD-2345");
    expect(await screen.findByText(/approved/i)).toBeInTheDocument();
  });

  it("denies with distinct copy", async () => {
    getDevicePending.mockResolvedValue(PENDING);
    denyDevice.mockResolvedValue({ approved: false });
    renderAt("?code=ABCD-2345");
    await userEvent.click(await screen.findByRole("button", { name: /deny/i }));
    expect(denyDevice).toHaveBeenCalledWith("ABCD-2345");
    expect(await screen.findByText(/declined/i)).toBeInTheDocument();
  });

  it("falls back to manual entry with no code in the URL", async () => {
    // The magic link opened in a different browser, so the sessionStorage
    // stash is gone. This is the conventional device-flow fallback and the
    // reason magic_link.py needs no `next` parameter.
    getDevicePending.mockResolvedValue(PENDING);
    renderAt("");
    const input = await screen.findByLabelText(/code/i);
    expect(getDevicePending).not.toHaveBeenCalled();
    await userEvent.type(input, "abcd2345");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    // The server normalizes case and hyphens, so assert the lookup ran --
    // not its exact spelling.
    expect(getDevicePending).toHaveBeenCalled();
    expect(await screen.findByText(/gaspode/)).toBeInTheDocument();
  });

  it("renders one message for unknown, decided and expired alike", async () => {
    getDevicePending.mockRejectedValue(new ApiError(404, "not found"));
    renderAt("?code=ZZZZ-9999");
    expect(await screen.findByText(/no longer waiting/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });
});
