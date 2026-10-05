/**
 * Footage page edits on one stage's clip of a multi-stage single take reach
 * the API naming that stage (#1212). Without it the server acts on the
 * first stage's registration of the shared file.
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
      moveAssignment: vi.fn(),
      removeVideo: vi.fn(),
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

const take = {
  name: "Test Match",
  stages: [1, 2, 3].map((n) => ({
    stage_number: n,
    stage_name: `Stage ${n}`,
    time_seconds: 20,
    videos: [
      {
        path: TAKE,
        video_id: `vid${n}`,
        role: "primary",
        beep_time: n * 100,
        beep_reviewed: true,
        processed: { beep: true, shot_detect: false, trim: false },
        match_timestamp: null,
      },
    ],
  })),
  unassigned_videos: [],
  raw_videos: [],
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

async function openStageClip(stage: number) {
  const ordinal = await screen.findByText(`0${stage}`, { selector: "td" });
  const row = ordinal.closest("tr") as HTMLElement;
  await userEvent.click(within(row).getByRole("button", { name: /take\.mp4/ }));
  return screen.findByRole("dialog", { name: "take.mp4" });
}

describe("Footage edits on a single take name the clip's stage", () => {
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
    vi.mocked(api.getProject).mockResolvedValue(take);
    vi.mocked(api.moveAssignment).mockResolvedValue(take);
    vi.mocked(api.removeVideo).mockResolvedValue({ project: take } as never);
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

  it("a role change on stage 3's clip sends stage 3", async () => {
    renderIngest();
    const sheet = await openStageClip(3);
    await userEvent.click(within(sheet).getByRole("button", { name: "Ignore" }));
    await waitFor(() => expect(api.moveAssignment).toHaveBeenCalledWith("alice", TAKE, 3, "ignored", 3));
  });

  it("removing stage 3's clip sends stage 3", async () => {
    renderIngest();
    const sheet = await openStageClip(3);
    await userEvent.click(within(sheet).getByRole("button", { name: "Remove video" }));
    // The confirm dialog's own "Remove video" button.
    const buttons = await screen.findAllByRole("button", { name: "Remove video" });
    await userEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(api.removeVideo).toHaveBeenCalledWith("alice", TAKE, false, 3));
  });
});
