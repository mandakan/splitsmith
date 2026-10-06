/**
 * A single take damaged by the old stage-1 bug shows one notice line on the
 * Footage page, and Repair sends it to the server (#1214).
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { api, type MatchProject, type ServerHealth } from "@/lib/api";
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
      repairTake: vi.fn(),
    },
  };
});

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: vi.fn(() => ({ mode: "local" as const, resolved: true })) };
});

vi.mock("@/lib/uploads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/uploads")>();
  return { ...actual, useUploads: vi.fn() };
});

const TAKE = "raw/take.mp4";

const entry = (n: number, id: string) => ({
  path: TAKE,
  video_id: id,
  role: "primary",
  beep_time: n * 100,
  beep_reviewed: true,
  processed: { beep: true, shot_detect: false, trim: false },
  match_timestamp: null,
});

function takeWith(videos: Record<number, unknown[]>): MatchProject {
  return {
    name: "Test Match",
    stages: [1, 2, 3].map((n) => ({ stage_number: n, stage_name: `Stage ${n}`, time_seconds: 20, videos: videos[n] ?? [] })),
    unassigned_videos: [],
    raw_videos: [{ storage_path: TAKE, original_filename: "take.mp4", covers_stages: [1, 2, 3] }],
    last_scanned_dir: null,
  } as unknown as MatchProject;
}

const damaged = takeWith({ 2: [entry(2, "vid2"), entry(1, "vid2")], 3: [entry(3, "vid3")] });
const healthy = takeWith({ 1: [entry(1, "vid1")], 2: [entry(2, "vid2")], 3: [entry(3, "vid3")] });

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

describe("Footage repair of a damaged single take", () => {
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
    vi.mocked(api.getProject).mockResolvedValue(damaged);
    vi.mocked(api.repairTake).mockResolvedValue({ repaired_stages: [1, 2], project: healthy });
    vi.mocked(api.getHealth).mockResolvedValue(health);
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/tmp/test",
      match_name: "Test Match",
      shooters: [{ slug: "alice", name: "Alice", selected_competitor_id: null }] as never,
      origin: "local",
      capabilities: ["edit", "review"],
    });
    vi.mocked(api.getBeepQueue).mockResolvedValue({
      total_items: 0,
      pending_count: 0,
      confirmed_count: 0,
      stages: [],
      origin: "local",
    } as never);
  });

  it("names the damaged take and repairs it", async () => {
    renderIngest();
    const line = await screen.findByText(
      "take.mp4 is listed twice on stage 02. Repair removes the extra entry; stage 01 may need its clip re-assigned.",
    );
    const row = line.parentElement as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Repair" }));
    await waitFor(() => expect(api.repairTake).toHaveBeenCalledWith("alice", "take.mp4"));
    await waitFor(() => expect(screen.queryByText(/is listed twice/)).toBeNull());
  });

  it("repairs by the stored file name, not the uploaded one", async () => {
    const renamed = { ...damaged, raw_videos: [{ storage_path: TAKE, original_filename: "GX010023.MP4", covers_stages: [1, 2, 3] }] };
    vi.mocked(api.getProject).mockResolvedValue(renamed as MatchProject);
    renderIngest();
    const line = await screen.findByText(/^GX010023\.MP4 is listed twice on stage 02\./);
    await userEvent.click(within(line.parentElement as HTMLElement).getByRole("button", { name: "Repair" }));
    await waitFor(() => expect(api.repairTake).toHaveBeenCalledWith("alice", "take.mp4"));
  });

  it("says nothing about a stage the user removed the clip from", async () => {
    vi.mocked(api.getProject).mockResolvedValue(takeWith({ 1: [entry(1, "vid1")], 2: [entry(2, "vid2")] }));
    renderIngest();
    await screen.findByText("Stage 3");
    expect(screen.queryByText(/is listed twice/)).toBeNull();
  });

  it("says nothing about a healthy take", async () => {
    vi.mocked(api.getProject).mockResolvedValue(healthy);
    renderIngest();
    await screen.findByText("Stage 3");
    expect(screen.queryByText(/is listed twice/)).toBeNull();
  });
});
