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
 * MatchShell.test.tsx and DesktopTokensSection.test.tsx mock api.
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
    useDeploymentMode: vi.fn(() => ({ mode: "local" as const, resolved: true })),
  };
});

function makeStatus(overrides: Partial<SyncStatusResponse> = {}): SyncStatusResponse {
  return {
    configured: true,
    last_synced_at: "2026-08-01T00:00:00Z",
    stale: false,
    pending_media: 0,
    errors: [],
    remote_changes: null,
    ...overrides,
  };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    kind: "sync_match",
    match_id: "m1",
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
    timings: null,
    created_at: "2026-08-07T00:00:00Z",
    updated_at: "2026-08-07T00:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

describe("SyncCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
    vi.mocked(api.getSyncSettings).mockResolvedValue({
      base_url: "https://splitsmith.app",
      token_set: true,
      account: null,
    });
  });

  it("renders nothing in hosted mode", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
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
    vi.mocked(api.startSync).mockResolvedValue(makeJob());

    render(<SyncCard jobs={[]} matchId="m1" />);

    const button = await screen.findByRole("button", { name: /sync now/i });
    expect(button).not.toBeDisabled();
    fireEvent.click(button);

    await waitFor(() => expect(api.startSync).toHaveBeenCalled());
  });

  it("keeps the button disabled after startSync resolves, before the jobs poller catches up", async () => {
    // Regression for the double-submit window: the jobs prop only
    // updates on MatchShell's poller (idle = 5s), so a naive
    // `starting`-only disable would re-enable the button for that
    // whole window while the sync_match job is genuinely pending
    // server-side. Against pre-fix code (no startedJob state) this
    // test fails: `starting` clears in the `finally` and nothing
    // else disables the button since the jobs prop is still [].
    vi.mocked(api.getSyncStatus).mockResolvedValue(
      makeStatus({ stale: true, pending_media: 1, errors: [] }),
    );
    vi.mocked(api.startSync).mockResolvedValue(makeJob());

    render(<SyncCard jobs={[]} matchId="m1" />);

    const button = await screen.findByRole("button", { name: /sync now/i });
    expect(button).not.toBeDisabled();
    fireEvent.click(button);

    // Wait for startSync to resolve and the component to settle -
    // `jobs` is still [] here, exactly as it would be before the
    // shell's next poll tick.
    await waitFor(() => expect(api.startSync).toHaveBeenCalled());
    await waitFor(() => expect(button).toBeDisabled());
  });

  it("shows progress for this match's own running sync job", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(
      makeStatus({ stale: true, pending_media: 3 }),
    );
    const own = makeJob({
      match_id: "m1",
      status: "running",
      message: "Uploading clip 3/9...",
    });

    render(<SyncCard jobs={[own]} matchId="m1" />);

    expect(await screen.findByText(/uploading clip 3\/9/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /syncing/i })).toBeDisabled();
  });

  it("does not mirror another match's running sync as its own", async () => {
    // Regression: the jobs list is global (cross-match). Pre-fix the
    // card matched sync_match jobs by kind alone, so match B's card
    // showed match A's progress message and claimed to be syncing.
    vi.mocked(api.getSyncStatus).mockResolvedValue(
      makeStatus({ stale: true, pending_media: 3 }),
    );
    const foreign = makeJob({
      match_id: "other-match",
      status: "running",
      message: "Uploading clip 3/9...",
    });

    render(<SyncCard jobs={[foreign]} matchId="m1" />);

    expect(
      await screen.findByText(/another match is syncing/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/uploading clip 3\/9/i)).not.toBeInTheDocument();
    // The button waits (one push at a time) but does not claim to be
    // syncing this match.
    const button = screen.getByRole("button", { name: /sync now/i });
    expect(button).toBeDisabled();
  });

  it("refetches status when the match changes", async () => {
    // Regression: the card is not remounted on match navigation
    // (react-router reuses the instance), so without a matchId-keyed
    // refetch match B kept showing match A's cached status - a
    // never-synced match read "0 files changed since last sync".
    vi.mocked(api.getSyncStatus).mockResolvedValue(
      makeStatus({ stale: true, pending_media: 3 }),
    );

    const { rerender } = render(<SyncCard jobs={[]} matchId="m1" />);
    expect(await screen.findByText(/3 files changed since last sync/i)).toBeInTheDocument();

    vi.mocked(api.getSyncStatus).mockResolvedValue(
      makeStatus({ last_synced_at: null, stale: true, pending_media: 0 }),
    );
    rerender(<SyncCard jobs={[]} matchId="m2" />);

    expect(await screen.findByText(/never synced/i)).toBeInTheDocument();
  });

  it("shows the hosted-has-newer hint when remote_changes > 0", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(makeStatus({ remote_changes: 2 }));

    render(<SyncCard jobs={[]} matchId="m1" />);

    expect(await screen.findByText(/hosted has newer changes/i)).toBeInTheDocument();
  });

  it("omits the hint when remote_changes is null", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(makeStatus({ remote_changes: null }));

    render(<SyncCard jobs={[]} matchId="m1" />);

    expect(await screen.findByText(/synced/i)).toBeInTheDocument();
    expect(screen.queryByText(/hosted has newer changes/i)).not.toBeInTheDocument();
  });
});
