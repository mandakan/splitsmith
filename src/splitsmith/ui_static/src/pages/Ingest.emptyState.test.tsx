/**
 * Ingest empty-state mode gating + hosted full-page drop
 * (add-videos UX rework, spec 2026-08-08).
 *
 * - nothing mode-specific renders before the deployment mode resolves
 *   (neutral skeleton only);
 * - local renders "Pick a folder" and no drop affordance at all;
 * - hosted renders "Browse files" and no picker affordance;
 * - a window-level drop on the hosted page enqueues into useUploads;
 * - the same drop in local mode enqueues nothing (DropGuard owns the
 *   toast; covered in DropGuard.test.tsx);
 * - a "hosted" mirror (desktop-origin project lacking the edit
 *   capability) never arms the drop listener at all - the misleading
 *   overlay must not show, let alone enqueue an upload that 403s on
 *   attachRawVideo (#756 review fix 1).
 * - that same mirror's empty-state card swaps its copy to a read-only
 *   explanation instead of inviting a drop the listener is deliberately
 *   unarmed for (#836).
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import {
  api,
  type BeepQueueResponse,
  type MatchProject,
  type ServerHealth,
  type ShooterListResponse,
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

// A desktop-origin mirror: "hosted" deployment mode (the SPA is served
// from the cloud), but the project's server-derived capability set lacks
// "edit" - the shape fix 1 targets.
const mirrorProject = {
  name: "Test Match",
  stages: [],
  unassigned_videos: [],
  last_scanned_dir: null,
  origin: "desktop",
  capabilities: ["review"],
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

const enqueue = vi.fn();

function mockUploads() {
  vi.mocked(useUploads).mockReturnValue({
    uploads: [],
    enqueue,
    cancel: vi.fn(),
    cancelAll: vi.fn(),
    clearFinished: vi.fn(),
    inFlight: false,
    attachTick: 0,
    probeFor: vi.fn(),
    queue: {},
  } as unknown as ReturnType<typeof useUploads>);
}

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

/** Flush pending effects before firing a *window-level* event.
 *
 *  `waitFor(... toBeDisabled())` is a render-time signal, and the
 *  read-only gate it observes also tears down `useWindowFileDrag`'s
 *  listener -- but React runs effect cleanup in a later commit than the
 *  render that triggered it. Asserting straight after the `waitFor` can
 *  therefore fire into a listener that is on its way out but still
 *  attached, and the width of that window depends on how busy the
 *  machine is: this failed under full-suite load and passed standalone
 *  and in CI. Awaiting an empty `act` drains the pending effects, so the
 *  assertion is about the gate rather than about scheduling.
 *
 *  Only needed where the event goes to `window`. Assertions on rendered
 *  output are already settled by the `waitFor` itself.
 */
async function settleEffects(): Promise<void> {
  await act(async () => {});
}

describe("Ingest empty state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUploads();
    vi.mocked(api.getProject).mockResolvedValue(emptyProject);
    vi.mocked(api.getHealth).mockResolvedValue(health);
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      shooters: [],
    } as unknown as ShooterListResponse);
    vi.mocked(api.getBeepQueue).mockResolvedValue({
      pending_count: 0,
    } as unknown as BeepQueueResponse);
  });

  it("renders a neutral skeleton until the mode resolves", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: false });
    renderIngest();
    expect(
      await screen.findByRole("status", { name: /checking how footage can be added/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pick a folder/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /browse files/i })).not.toBeInTheDocument();
  });

  it("local mode renders the picker card and no drop affordance", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
    renderIngest();
    expect(await screen.findByRole("button", { name: /pick a folder/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /browse files/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/drop/i)).not.toBeInTheDocument();
  });

  it("hosted mode renders the upload card and no picker affordance", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    renderIngest();
    expect(await screen.findByRole("button", { name: /browse files/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pick a folder/i })).not.toBeInTheDocument();
  });

  it("hosted mode enqueues a window-level drop", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    renderIngest();
    await screen.findByRole("button", { name: /browse files/i });
    const file = new File(["x"], "GH010001.MP4", { type: "video/mp4" });
    act(() => {
      fireEvent.drop(window, { dataTransfer: { files: [file], types: ["Files"] } });
    });
    expect(enqueue).toHaveBeenCalledTimes(1);
    const [files, ctx] = enqueue.mock.calls[0];
    expect(Array.from(files as FileList)).toHaveLength(1);
    expect(ctx).toEqual({ slug: "alice", stages: [] });
  });

  it("local mode never enqueues a window-level drop", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
    renderIngest();
    await screen.findByRole("button", { name: /pick a folder/i });
    const file = new File(["x"], "GH010001.MP4", { type: "video/mp4" });
    act(() => {
      fireEvent.drop(window, { dataTransfer: { files: [file], types: ["Files"] } });
    });
    expect(enqueue).not.toHaveBeenCalled();
  });

  it("a hosted mirror without edit never enqueues a window-level drop (#756)", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    vi.mocked(api.getProject).mockResolvedValue(mirrorProject);
    renderIngest();
    // The project loads async; the gate is optimistic (editDenied=false)
    // until it resolves, so wait for the settled, disabled control
    // before asserting the drop is a no-op - otherwise the drop can race
    // ahead of the project fetch and pass for the wrong reason.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /browse files/i })).toBeDisabled(),
    );
    await settleEffects();
    const file = new File(["x"], "GH010001.MP4", { type: "video/mp4" });
    act(() => {
      fireEvent.drop(window, { dataTransfer: { files: [file], types: ["Files"] } });
    });
    expect(enqueue).not.toHaveBeenCalled();
  });

  it("a hosted mirror without edit never arms the full-page drop overlay (#756)", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    vi.mocked(api.getProject).mockResolvedValue(mirrorProject);
    renderIngest();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /browse files/i })).toBeDisabled(),
    );
    await settleEffects();
    const file = new File(["x"], "GH010001.MP4", { type: "video/mp4" });
    act(() => {
      fireEvent.dragEnter(window, { dataTransfer: { files: [file], types: ["Files"] } });
    });
    // hostedDropActive gates useWindowFileDrag's `enabled` -- if it's
    // false, the hook never attaches its dragenter listener, so the
    // full-page "Drop videos to upload" overlay must never appear even
    // after a real drag-enter.
    expect(screen.queryByText(/drop videos to upload/i)).not.toBeInTheDocument();
  });

  it("a hosted mirror without edit shows read-only copy instead of the drop invite (#836)", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    vi.mocked(api.getProject).mockResolvedValue(mirrorProject);
    renderIngest();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /browse files/i })).toBeDisabled(),
    );
    expect(
      screen.getByText(
        /footage for this match is added on the desktop install and syncs here/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/drop video files anywhere on this page/i),
    ).not.toBeInTheDocument();
  });

  it("hosted mode with edit keeps the original drop-invite copy", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    renderIngest();
    await screen.findByRole("button", { name: /browse files/i });
    expect(
      screen.getByText(/drop video files anywhere on this page/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        /footage for this match is added on the desktop install and syncs here/i,
      ),
    ).not.toBeInTheDocument();
  });
});
