/**
 * SyncSettingsDialog save rules (#719).
 *
 * The one that matters: a base URL saves on its own, with no token.
 * This dialog is the only setter of ``hosted_base_url`` in the SPA, and
 * the device flow refuses to start until that is set -- so a token
 * requirement here made the paste-a-token fallback a precondition for
 * the very path that exists to replace it. A fresh install could not
 * reach the device flow at all.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type HostedSyncSettings } from "@/lib/api";

import { SyncSettingsDialog } from "@/components/match/SyncSettingsDialog";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      putSyncSettings: vi.fn(),
      getAutoSync: vi.fn(),
      setGlobalAutoSync: vi.fn(),
    },
  };
});

/** A fresh install: no base URL, no token, no linked account. */
const FRESH: HostedSyncSettings = { base_url: null, token_set: false, account: null };

describe("SyncSettingsDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getAutoSync).mockRejectedValue(new Error("no route"));
  });

  it("saves a base URL on its own from a fresh config", async () => {
    const saved: HostedSyncSettings = {
      base_url: "https://splitsmith.app",
      token_set: false,
      account: null,
    };
    vi.mocked(api.putSyncSettings).mockResolvedValue(saved);
    const onSaved = vi.fn();
    const onClose = vi.fn();

    render(
      <SyncSettingsDialog settings={FRESH} onClose={onClose} onSaved={onSaved} />,
    );

    fireEvent.change(screen.getByLabelText(/base url/i), {
      target: { value: "https://splitsmith.app" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    // Persisted with an explicit null token -- the backend contract for
    // "leave whatever is stored alone", which on a fresh config means
    // "store no token at all".
    await waitFor(() =>
      expect(api.putSyncSettings).toHaveBeenCalledWith("https://splitsmith.app", null),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(onClose).toHaveBeenCalled();
    // No "Token is required." (or any other) banner on the way out.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("still refuses to save with no base URL", async () => {
    render(
      <SyncSettingsDialog settings={FRESH} onClose={vi.fn()} onSaved={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/base url is required/i);
    expect(api.putSyncSettings).not.toHaveBeenCalled();
  });

  it("passes a typed token through when the operator uses the fallback", async () => {
    vi.mocked(api.putSyncSettings).mockResolvedValue({
      base_url: "https://splitsmith.app",
      token_set: true,
      account: null,
    });

    render(
      <SyncSettingsDialog settings={FRESH} onClose={vi.fn()} onSaved={vi.fn()} />,
    );

    fireEvent.change(screen.getByLabelText(/base url/i), {
      target: { value: "https://splitsmith.app" },
    });
    fireEvent.change(screen.getByLabelText(/desktop token/i), {
      target: { value: "pasted-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(api.putSyncSettings).toHaveBeenCalledWith(
        "https://splitsmith.app",
        "pasted-token",
      ),
    );
  });
  it("switches auto-sync off for this computer", async () => {
    vi.mocked(api.getAutoSync).mockResolvedValue({
      enabled: true,
      setting: null,
      global_enabled: true,
      paused_reason: null,
      last_auto: null,
    });
    vi.mocked(api.setGlobalAutoSync).mockResolvedValue({ global_enabled: false });
    render(<SyncSettingsDialog settings={FRESH} onClose={vi.fn()} onSaved={vi.fn()} />);
    const group = await screen.findByRole("group", { name: "Auto-sync on this computer" });
    expect(within(group).getByRole("button", { name: "On" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(group).getByRole("button", { name: "Off" }));
    await waitFor(() => expect(api.setGlobalAutoSync).toHaveBeenCalledWith(false));
    await waitFor(() =>
      expect(within(group).getByRole("button", { name: "Off" })).toHaveAttribute("aria-pressed", "true"),
    );
  });
});
