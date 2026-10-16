/**
 * Making a clip primary on its own stage goes through the audit-safe swap:
 * with audit work on the stage it asks first, then the server keeps the
 * audit as a backup and redoes the stage on the new primary. The plain move
 * it used to send left the audit on the old camera's clock (2026-10-16).
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { ApiError, api, type MatchProject, type ServerHealth } from "@/lib/api";
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
      swapPrimary: vi.fn(),
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

const PHONE = "raw/IMG_2196.MOV";
const HEAD = "raw/VID_20260412_182407_00_059.mp4";

const video = (path: string, role: string, id: string) => ({
  path,
  video_id: id,
  role,
  beep_time: 5,
  beep_reviewed: true,
  processed: { beep: true, shot_detect: true, trim: true },
  match_timestamp: null,
});

const project = {
  name: "Test Match",
  stages: [
    {
      stage_number: 1,
      stage_name: "Stage 1",
      time_seconds: 20,
      videos: [video(PHONE, "primary", "phone1"), video(HEAD, "secondary", "head1")],
    },
  ],
  unassigned_videos: [],
  raw_videos: [],
  last_scanned_dir: null,
} as unknown as MatchProject;
const take = project;

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

async function openHeadClip() {
  const ordinal = await screen.findByText("01", { selector: "td" });
  const row = ordinal.closest("tr") as HTMLElement;
  await userEvent.click(within(row).getByRole("button", { name: /VID_20260412_182407_00_059/ }));
  return screen.findByRole("dialog", { name: "VID_20260412_182407_00_059.mp4" });
}

describe("Making a clip primary", () => {
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

  it("asks before swapping a stage with audit work, then swaps", async () => {
    vi.mocked(api.swapPrimary)
      .mockRejectedValueOnce(
        new ApiError(409, "audit_exists", {
          detail: { code: "audit_exists", current_primary: PHONE, new_primary: HEAD, stage_number: 1 },
        }),
      )
      .mockResolvedValueOnce(project);
    renderIngest();
    const sheet = await openHeadClip();
    await userEvent.click(within(sheet).getByRole("button", { name: "Primary" }));

    const dialog = await screen.findByRole("dialog", { name: /primary on stage 1/ });
    expect(within(dialog).getByText(/IMG_2196\.MOV/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Make primary" }));

    await waitFor(() => expect(api.swapPrimary).toHaveBeenLastCalledWith("alice", HEAD, 1, true));
    expect(api.swapPrimary).toHaveBeenCalledWith("alice", HEAD, 1, false);
    expect(api.moveAssignment).not.toHaveBeenCalled();
  });

  it("does nothing when the swap is cancelled", async () => {
    vi.mocked(api.swapPrimary).mockRejectedValueOnce(
      new ApiError(409, "audit_exists", { detail: { code: "audit_exists", current_primary: PHONE } }),
    );
    renderIngest();
    const sheet = await openHeadClip();
    await userEvent.click(within(sheet).getByRole("button", { name: "Primary" }));
    const dialog = await screen.findByRole("dialog", { name: /primary on stage 1/ });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /primary on stage 1/ })).toBeNull());
    expect(api.swapPrimary).toHaveBeenCalledTimes(1);
  });

  it("a click on the clip's own role changes nothing", async () => {
    renderIngest();
    const sheet = await openHeadClip();
    await userEvent.click(within(sheet).getByRole("button", { name: "Secondary" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(api.swapPrimary).not.toHaveBeenCalled();
    expect(api.moveAssignment).not.toHaveBeenCalled();
  });

  it("swaps without asking when the stage has no audit work", async () => {
    vi.mocked(api.swapPrimary).mockResolvedValueOnce(project);
    renderIngest();
    const sheet = await openHeadClip();
    await userEvent.click(within(sheet).getByRole("button", { name: "Primary" }));
    await waitFor(() => expect(api.swapPrimary).toHaveBeenCalledWith("alice", HEAD, 1, false));
    expect(screen.queryByRole("dialog", { name: /primary on stage 1/ })).toBeNull();
    expect(api.moveAssignment).not.toHaveBeenCalled();
  });
});
