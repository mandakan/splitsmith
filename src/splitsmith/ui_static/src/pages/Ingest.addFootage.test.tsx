/**
 * Ingest local add-footage flow through the rewritten FolderPicker:
 * open picker, commit, scan fires with the chosen storage mode, the
 * dialog closes, the project reloads. Also pins allowEmptyFolder ON
 * for this call site (spec: whole-folder commits stay valid when no
 * top-level videos show - the backend scan walks recursively).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import {
  api,
  type FsListing,
  type MatchProject,
  type ServerHealth,
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
    useDeploymentMode: vi.fn(() => ({ mode: "local" as const, resolved: true })),
  };
});

vi.mock("@/lib/uploads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/uploads")>();
  return { ...actual, useUploads: vi.fn() };
});

const emptyProject = {
  name: "Test Match",
  stages: [],
  unassigned_videos: [],
  last_scanned_dir: null,
} as unknown as MatchProject;

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

const listing: FsListing = {
  path: "/Users/op/Movies",
  parent: "/Users/op",
  entries: [
    {
      name: "GH010001.MP4",
      kind: "video",
      video_count: null,
      size_bytes: 1024,
      mtime: 1754600100,
      duration: null,
      thumbnail_url: null,
    },
  ],
  suggested_starts: [],
};

const emptyListing: FsListing = { ...listing, entries: [] };

function renderIngest() {
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1/ingest/alice"]}>
        <Routes>
          <Route path="/match/:matchId/ingest/:slug" element={<Ingest />} />
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

describe("Ingest add-footage (local)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
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
    vi.mocked(api.getProject).mockResolvedValue(emptyProject);
    vi.mocked(api.getHealth).mockResolvedValue(health);
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/tmp/test",
      match_name: "Test Match",
      shooters: [],
      origin: "local",
    });
    vi.mocked(api.getBeepQueue).mockResolvedValue({
      total_items: 0,
      pending_count: 0,
      confirmed_count: 0,
      stages: [],
    });
    vi.mocked(api.listFolder).mockResolvedValue(listing);
    vi.mocked(api.scanVideos).mockResolvedValue({
      registered: ["/Users/op/Movies/GH010001.MP4"],
      auto_assigned: {},
      skipped: [],
    });
  });

  it("commits a folder with the picked storage mode and closes", async () => {
    const user = userEvent.setup();
    renderIngest();
    await user.click(await screen.findByRole("button", { name: /pick a folder/i }));
    const dialog = await screen.findByRole("dialog", { name: /add footage/i });
    expect(dialog).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /copy into project/i }));
    await user.click(screen.getByRole("button", { name: /add this folder/i }));
    await waitFor(() =>
      expect(api.scanVideos).toHaveBeenCalledWith(
        "alice",
        "/Users/op/Movies",
        true,
        "copy",
      ),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: /add footage/i })).not.toBeInTheDocument(),
    );
    // Reloaded after import: initial load + afterImport reload.
    expect(vi.mocked(api.getProject).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the folder commit enabled when the folder shows no direct videos (allowEmptyFolder on)", async () => {
    vi.mocked(api.listFolder).mockResolvedValue(emptyListing);
    const user = userEvent.setup();
    renderIngest();
    await user.click(await screen.findByRole("button", { name: /pick a folder/i }));
    await screen.findByRole("dialog", { name: /add footage/i });
    const commit = await screen.findByRole("button", { name: /add this folder/i });
    expect(commit).toBeEnabled();
    await user.click(commit);
    await waitFor(() =>
      expect(api.scanVideos).toHaveBeenCalledWith(
        "alice",
        "/Users/op/Movies",
        true,
        "symlink",
      ),
    );
  });
});
