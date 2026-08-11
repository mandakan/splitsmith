/**
 * Ingest proxy-generation poll (#821 follow-up).
 *
 * get_project's honesty fix means a desktop-mirror match reports every
 * video's proxy_ready as false forever (mirror matches never get proxies --
 * raw media stays on desktop). The poll effect must not arm in that case,
 * or it loops every 5s against hosted for the lifetime of the page. A
 * non-mirror project with the same pending video is the control: it must
 * still arm the poll, proving the assertion below actually exercises the
 * gate rather than some unrelated reason setInterval never fires.
 */
import { render, screen, waitFor } from "@testing-library/react";
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

function pendingProject(origin: "desktop" | "local"): MatchProject {
  return {
    name: "Test Match",
    stages: [],
    unassigned_videos: [{ video_id: "v1", path: "GH010001.MP4", proxy_ready: false }],
    last_scanned_dir: null,
    origin,
  } as unknown as MatchProject;
}

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

describe("Ingest proxy poll", () => {
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
      origin: "local",
    });
  });

  // testing-library's own waitFor/findBy polling uses setInterval
  // internally (a 50ms housekeeping tick), so the assertions below filter
  // for the poll's actual 5000ms delay rather than any call at all.
  function pollIntervalCalls(spy: ReturnType<typeof vi.spyOn>) {
    return spy.mock.calls.filter(([, delay]: [unknown, number?]) => delay === 5000);
  }

  it("never arms the poll on a mirror match, even with a pending proxy", async () => {
    vi.mocked(api.getProject).mockResolvedValue(pendingProject("desktop"));
    const setIntervalSpy = vi.spyOn(window, "setInterval");
    renderIngest();
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
    // Let any effects scheduled off the resolved project settle.
    await screen.findByRole("heading", { level: 1, name: /add footage/i });
    expect(pollIntervalCalls(setIntervalSpy)).toHaveLength(0);
  });

  it("arms the poll on a non-mirror match with a pending proxy (control)", async () => {
    vi.mocked(api.getProject).mockResolvedValue(pendingProject("local"));
    const setIntervalSpy = vi.spyOn(window, "setInterval");
    renderIngest();
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
    await screen.findByRole("heading", { level: 1, name: /add footage/i });
    await waitFor(() => expect(pollIntervalCalls(setIntervalSpy).length).toBeGreaterThan(0));
  });
});
