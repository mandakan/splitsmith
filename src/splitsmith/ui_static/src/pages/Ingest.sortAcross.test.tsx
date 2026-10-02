/**
 * Footage page, local mode, two shooters: "Sort across shooters" starts the
 * footage sort over every unassigned clip with no folder picker, and the
 * Add footage picker (the sort, once there are scorecards) accepts a folder holding only
 * subfolders (2026-10-02: it did not, and the user ended up importing the
 * parent folder as one shooter's footage instead).
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import {
  api,
  type FsListing,
  type MatchProject,
  type ServerHealth,
  type ShooterListEntry,
} from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { useUploads } from "@/lib/uploads";
import { Ingest } from "@/pages/Ingest";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      getHealth: vi.fn(),
      listMatchShooters: vi.fn(),
      getBeepQueue: vi.fn(),
      listFolder: vi.fn(),
      scanVideos: vi.fn(),
      scanFiles: vi.fn(),
      startFootageSort: vi.fn(),
      startFootageSortFiles: vi.fn(),
      startFootageSortUnassigned: vi.fn(),
      listFootageSorts: vi.fn(),
      discardFootageSort: vi.fn(),
      probeFile: vi.fn().mockResolvedValue({
        duration: null,
        thumbnail_url: null,
        width: null,
        height: null,
        codec: null,
        size_bytes: null,
      }),
    },
  };
});

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: vi.fn(() => ({
      mode: "local" as const,
      resolved: true,
    })),
  };
});

vi.mock("@/lib/uploads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/uploads")>();
  return { ...actual, useUploads: vi.fn() };
});

const alicesProject = {
  name: "Test Match",
  stages: [
    {
      stage_number: 1,
      stage_name: "S1",
      time_seconds: 10,
      scorecard_updated_at: "2026-09-26T11:05:00Z",
      videos: [],
    },
  ],
  unassigned_videos: [
    {
      path: "raw/IMG_0001.MOV",
      role: "secondary",
      processed: {},
      match_timestamp: "2026-09-26T11:00:00Z",
    },
  ],
  last_scanned_dir: "/Volumes/X9/raw/2026-hostfinalen/from-anton",
} as unknown as MatchProject;
const bobsProject = {
  ...alicesProject,
  unassigned_videos: [],
  last_scanned_dir: null,
} as unknown as MatchProject;

const shooter = (slug: string, name: string) =>
  ({
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: 0,
    stages_total: 1,
    video_count: 0,
    cameras: [],
  }) as unknown as ShooterListEntry;

const health = {
  status: "ok",
  bound: true,
  project_name: "Test Match",
  project_root: "/tmp/test",
  match_id: "m1",
  kind: "match",
  default_shooter_slug: "alice",
  schema_version: 1,
} as unknown as ServerHealth;

const parentListing: FsListing = {
  path: "/Volumes/X9/raw/2026-hostfinalen",
  parent: "/Volumes/X9/raw",
  entries: [
    {
      name: "from-anton",
      kind: "dir",
      video_count: 20,
      size_bytes: null,
      mtime: null,
      duration: null,
      thumbnail_url: null,
    },
  ],
  suggested_starts: [],
} as unknown as FsListing;

/** The match shell's outlet context, enough for Footage's default-shooter
 *  redirect. */
function OutletWithShooters() {
  return (
    <Outlet
      context={{
        shooters: [shooter("alice", "Alice"), shooter("bob", "Bob")],
        capabilities: ["edit", "review"],
        jobs: [],
      }}
    />
  );
}

function renderIngest() {
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1/ingest/alice"]}>
        <Routes>
          <Route path="/match/:matchId/ingest/:slug" element={<Ingest />} />
          <Route
            path="/match/:matchId/footage-sort/:scanId"
            element={<p>sort review</p>}
          />
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

describe("Ingest sort across shooters (local)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useDeploymentMode).mockReturnValue({
      mode: "local",
      resolved: true,
    });
    vi.mocked(useUploads).mockReturnValue({
      uploads: [],
      enqueue: vi.fn(),
      cancel: vi.fn(),
      cancelAll: vi.fn(),
      clearFinished: vi.fn(),
      inFlight: false,
      attachTick: 0,
      probeFor: vi.fn(),
      queue: {},
    } as unknown as ReturnType<typeof useUploads>);
    vi.mocked(api.getProject).mockImplementation(async (slug: string) =>
      slug === "alice" ? alicesProject : bobsProject,
    );
    vi.mocked(api.getHealth).mockResolvedValue(health);
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/tmp/test",
      match_name: "Test Match",
      shooters: [shooter("alice", "Alice"), shooter("bob", "Bob")],
      origin: "local",
      capabilities: ["edit", "review"],
    });
    vi.mocked(api.getBeepQueue).mockResolvedValue({
      total_items: 0,
      pending_count: 0,
      confirmed_count: 0,
      stages: [],
      origin: "local",
    } as unknown as Awaited<ReturnType<typeof api.getBeepQueue>>);
    vi.mocked(api.listFolder).mockResolvedValue(parentListing);
    vi.mocked(api.startFootageSort).mockResolvedValue({ scan_id: "s1" });
    vi.mocked(api.startFootageSortUnassigned).mockResolvedValue({
      scan_id: "s2",
    });
    vi.mocked(api.listFootageSorts).mockResolvedValue([]);
    vi.mocked(api.discardFootageSort).mockResolvedValue({
      scan_id: "s9",
      source_dir: "/Volumes/X9/raw/2026-hostfinalen",
      created_at: "2026-10-02T08:00:00Z",
      status: "discarded",
      clips: 40,
      to_review: 0,
    });
  });

  it("sorts every unassigned clip straight from the Unassigned panel, no picker", async () => {
    const user = userEvent.setup();
    renderIngest();

    const panel = await screen.findByRole("region", {
      name: "Unassigned videos",
    });
    await user.click(
      within(panel).getByRole("button", { name: "Sort across shooters" }),
    );

    await waitFor(() =>
      expect(api.startFootageSortUnassigned).toHaveBeenCalledTimes(1),
    );
    expect(await screen.findByText("sort review")).toBeInTheDocument();
    expect(api.scanVideos).not.toHaveBeenCalled();
    expect(api.startFootageSort).not.toHaveBeenCalled();
  });

  it("Add footage sorts a folder that only holds subfolders", async () => {
    const user = userEvent.setup();
    renderIngest();

    await user.click(
      await screen.findByRole("button", { name: "Add footage" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Add footage",
    });
    const sort = await within(dialog).findByRole("button", {
      name: "Sort this folder",
    });
    await waitFor(() => expect(sort).toBeEnabled());
    await user.click(sort);

    await waitFor(() =>
      expect(api.startFootageSort).toHaveBeenCalledWith(
        "/Volumes/X9/raw/2026-hostfinalen",
      ),
    );
    expect(api.scanVideos).not.toHaveBeenCalled();
  });

  it("keeps the per-shooter import for a match without scorecards", async () => {
    const bare = {
      ...alicesProject,
      stages: [
        { stage_number: 1, stage_name: "S1", time_seconds: 10, videos: [] },
      ],
    } as unknown as MatchProject;
    vi.mocked(api.getProject).mockResolvedValue(bare);
    const user = userEvent.setup();
    renderIngest();

    await user.click(
      await screen.findByRole("button", { name: "Add footage" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Add footage" });

    expect(
      await within(dialog).findByRole("button", { name: "Add this folder" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Sort across shooters" }),
    ).not.toBeInTheDocument();
  });

  it("offers an unfinished sort to continue or discard", async () => {
    vi.mocked(api.listFootageSorts).mockResolvedValue([
      {
        scan_id: "s9",
        source_dir: "/Volumes/X9/raw/2026-hostfinalen",
        created_at: "2026-10-02T08:00:00Z",
        status: "ready",
        clips: 40,
        to_review: 26,
      },
    ]);
    const user = userEvent.setup();
    renderIngest();

    expect(
      await screen.findByText(
        "Sort of 2026-hostfinalen: 26 clips left to place",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continue" })).toHaveAttribute(
      "href",
      "/match/m1/footage-sort/s9",
    );
    await user.click(screen.getByRole("button", { name: "Discard" }));
    await user.click(
      await screen.findByRole("button", { name: "Discard sort" }),
    );

    await waitFor(() =>
      expect(api.discardFootageSort).toHaveBeenCalledWith("s9"),
    );
    await waitFor(() =>
      expect(
        screen.queryByText(/26 clips left to place/),
      ).not.toBeInTheDocument(),
    );
  });

  it("shows the sort's import summary after the redirect to a shooter", async () => {
    render(
      <ConfirmProvider>
        <MemoryRouter
          initialEntries={[
            {
              pathname: "/match/m1/ingest",
              state: { sortImported: "Imported 40 clips: Alice 25, Bob 15" },
            },
          ]}
        >
          <Routes>
            <Route path="/match/:matchId" element={<OutletWithShooters />}>
              <Route path="ingest" element={<Ingest />} />
              <Route path="ingest/:slug" element={<Ingest />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ConfirmProvider>,
    );

    expect(
      await screen.findByText("Imported 40 clips: Alice 25, Bob 15"),
    ).toBeInTheDocument();
  });

  it("offers the beep review queue when beeps wait for confirmation", async () => {
    vi.mocked(api.getBeepQueue).mockResolvedValue({
      total_items: 1,
      pending_count: 1,
      confirmed_count: 0,
      stages: [
        {
          stage_number: 1,
          stage_name: "S1",
          items: [
            {
              slug: "bob",
              shooter_name: "Bob",
              stage_number: 1,
              stage_name: "S1",
              role: "primary",
              video_id: "b1",
              video_path: "raw/IMG_1.MOV",
              beep_time: 4,
              beep_confidence: 0.9,
              beep_reviewed: false,
              status: "unreviewed",
              alt_candidates: [],
              proxy_ready: true,
              snippet_ready: false,
              trim_stale: false,
            },
          ],
        },
      ],
      origin: "local",
      capabilities: ["edit", "review"],
    } as never);
    renderIngest();

    const link = await screen.findByRole("link", {
      name: "Review beeps · 1 to confirm",
    });

    expect(link).toHaveAttribute("href", "/match/m1/audit/bob/1?cam=b1");
  });
});
