/**
 * SyncCard - local-only hosted-sync status card on MatchOverview
 * (#631 Task 11).
 *
 * Floor per the task brief: hosted mode renders nothing (local-only
 * feature); unconfigured shows the setup CTA; a stale match shows the
 * changed-file count; plan errors are listed AND disable the sync
 * button (Task 9's review finding - an enabled button that would
 * always fail is worse than a disabled one); clicking the button
 * fires startSync. Mocks @/lib/api and @/lib/features the same way
 * MatchShell.test.tsx and DesktopTokensDialog.test.tsx mock api.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Job, type SyncStatusResponse } from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";

import { SyncCard } from "@/components/match/SyncCard";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSyncStatus: vi.fn(),
      getSyncSettings: vi.fn(),
      startSync: vi.fn(),
      putSyncSettings: vi.fn(),
    },
  };
});

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: vi.fn(() => "local"),
  };
});

function makeStatus(overrides: Partial<SyncStatusResponse> = {}): SyncStatusResponse {
  return {
    configured: true,
    last_synced_at: "2026-08-01T00:00:00Z",
    stale: false,
    pending_media: 0,
    errors: [],
    ...overrides,
  };
}

describe("SyncCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useDeploymentMode).mockReturnValue("local");
    vi.mocked(api.getSyncSettings).mockResolvedValue({
      base_url: "https://splitsmith.app",
      token_set: true,
    });
  });

  it("renders nothing in hosted mode", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue("hosted");
    vi.mocked(api.getSyncStatus).mockResolvedValue(makeStatus());

    const { container } = render(<SyncCard jobs={[]} matchId="m1" />);

    // Give any stray effect a tick to run, then assert nothing rendered
    // and the local-only endpoints were never even called.
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
    expect(api.getSyncStatus).not.toHaveBeenCalled();
  });

  it("shows the setup CTA when sync is not configured", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(
      makeStatus({ configured: false, last_synced_at: null, stale: true }),
    );

    render(<SyncCard jobs={[]} matchId="m1" />);

    expect(
      await screen.findByRole("button", { name: /set up hosted sync/i }),
    ).toBeInTheDocument();
  });

  it("shows the changed-file count when stale", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(
      makeStatus({ stale: true, pending_media: 3, errors: [] }),
    );

    render(<SyncCard jobs={[]} matchId="m1" />);

    expect(await screen.findByText(/3 files changed since last sync/i)).toBeInTheDocument();
  });

  it("lists plan errors and disables the sync button", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(
      makeStatus({
        stale: true,
        pending_media: 2,
        errors: ["clip.mp4 lives outside the match root"],
      }),
    );

    render(<SyncCard jobs={[]} matchId="m1" />);

    expect(
      await screen.findByText(/clip\.mp4 lives outside the match root/i),
    ).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /sync now/i });
    expect(button).toBeDisabled();
  });

  it("fires startSync when the sync button is clicked", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(
      makeStatus({ stale: true, pending_media: 1, errors: [] }),
    );
    vi.mocked(api.startSync).mockResolvedValue({
      id: "job-1",
      kind: "sync_match",
      stage_number: null,
      shooter_slug: null,
      video_id: null,
      status: "pending",
      progress: null,
      message: null,
      error: null,
      cancel_requested: false,
      acknowledged: false,
      result: null,
      created_at: "2026-08-07T00:00:00Z",
      updated_at: "2026-08-07T00:00:00Z",
      started_at: null,
      finished_at: null,
    } satisfies Job);

    render(<SyncCard jobs={[]} matchId="m1" />);

    const button = await screen.findByRole("button", { name: /sync now/i });
    expect(button).not.toBeDisabled();
    fireEvent.click(button);

    await waitFor(() => expect(api.startSync).toHaveBeenCalled());
  });
});
