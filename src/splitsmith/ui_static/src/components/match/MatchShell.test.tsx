/**
 * MatchShell job-settlement refetch (#663).
 *
 * The sidebar stage list renders from the shell's one-shot project
 * snapshot. These tests pin the fix: when a background job leaves the
 * active set (running -> succeeded), the shell must re-fetch the
 * project (and the beep queue) so stage status dots update without a
 * manual reload - and must NOT refetch on every poll tick while jobs
 * are still running.
 *
 * Real timers: useJobs polls at 1s while anything is active, so the
 * transition lands within ~1.2s of render.
 */
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type Job,
  type MatchProject,
  type ServerHealth,
  type ShooterListEntry,
} from "@/lib/api";
import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";

import { MatchShell } from "@/components/match/MatchShell";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getHealth: vi.fn(),
      getScoreboardIdentity: vi.fn(),
      getServerFeatures: vi.fn(),
      getMe: vi.fn(),
      listMatchShooters: vi.fn(),
      getProject: vi.fn(),
      getBeepQueue: vi.fn(),
      listJobs: vi.fn(),
    },
  };
});

function makeShooter(slug: string, name: string): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: 0,
    stages_total: 1,
    video_count: 1,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: [],
  };
}

function makeProject(): MatchProject {
  return {
    schema_version: 1,
    name: "bromma-2026",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    competitor_name: null,
    scoreboard_match_id: null,
    scoreboard_content_type: null,
    selected_shooter_id: null,
    selected_competitor_id: null,
    shooter_token: null,
    match_date: null,
    stages: [
      {
        stage_number: 1,
        stage_name: "Stage One",
        time_seconds: 20,
        scorecard_updated_at: null,
        videos: [],
        skipped: false,
        placeholder: false,
        time_seconds_manual: false,
        stage_rounds: null,
        scorecard: null,
      },
    ],
    unassigned_videos: [],
    last_scanned_dir: null,
    raw_dir: null,
    audio_dir: null,
    trimmed_dir: null,
    exports_dir: null,
    probes_dir: null,
    thumbs_dir: null,
    trim_pre_buffer_seconds: 5,
    trim_post_buffer_seconds: 5,
    automation: {},
    nudges_dismissed_stages: [],
    compare_camera: null,
    raw_videos: [],
  };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    kind: "shot_detect",
    stage_number: 1,
    shooter_slug: "mathias",
    video_id: null,
    status: "running",
    progress: 0.5,
    message: null,
    error: null,
    cancel_requested: false,
    acknowledged: false,
    result: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: null,
    ...overrides,
  };
}

const HEALTH: ServerHealth = {
  status: "ok",
  version: "0.0.0-test",
  bound: false,
  project_name: "bromma-2026",
  project_root: "/root/bromma-2026",
  match_id: "m1",
  kind: "match",
  default_shooter_slug: "mathias",
  schema_version: 1,
};

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function stubMatchMedia() {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function setUpApi(listJobsImpl: () => Promise<Job[]>) {
  vi.mocked(api.getHealth).mockResolvedValue(HEALTH);
  vi.mocked(api.getScoreboardIdentity).mockResolvedValue(null);
  vi.mocked(api.getServerFeatures).mockResolvedValue({
    lab: false,
    mode: "local",
  });
  vi.mocked(api.getMe).mockResolvedValue({
    id: "local",
    email: "local@localhost",
    display_name: null,
    is_admin: false,
  });
  vi.mocked(api.listMatchShooters).mockResolvedValue({
    match_root: "/root",
    match_name: "Bromma Classic 2026",
    shooters: [makeShooter("mathias", "Mathias")],
  });
  vi.mocked(api.getProject).mockResolvedValue(makeProject());
  vi.mocked(api.getBeepQueue).mockResolvedValue({
    total_items: 0,
    pending_count: 0,
    confirmed_count: 0,
    stages: [],
  });
  vi.mocked(api.listJobs).mockImplementation(listJobsImpl);
}

function renderShell() {
  return render(
    <ModeProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={["/audit/mathias/1"]}>
          <Routes>
            <Route element={<MatchShell />}>
              <Route path="/audit/:slug/:stage" element={<div>page</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </ModeProvider>,
  );
}

describe("MatchShell job settlement (#663)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stubMatchMedia();
    window.ResizeObserver =
      ResizeObserverStub as unknown as typeof window.ResizeObserver;
  });

  it("refetches the project and beep queue when a job leaves the active set", async () => {
    // First poll sees the job running; every later poll sees it done.
    let polls = 0;
    setUpApi(() => {
      polls += 1;
      return Promise.resolve([
        makeJob({ status: polls === 1 ? "running" : "succeeded" }),
      ]);
    });
    renderShell();

    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
    expect(api.getProject).toHaveBeenCalledWith("mathias");
    await waitFor(() => expect(api.getBeepQueue).toHaveBeenCalledTimes(1));

    // The 1s active poll observes running -> succeeded; the shell must
    // invalidate its snapshot.
    await waitFor(
      () => expect(api.getProject).toHaveBeenCalledTimes(2),
      { timeout: 4000 },
    );
    expect(api.getProject).toHaveBeenLastCalledWith("mathias");
    await waitFor(() => expect(api.getBeepQueue).toHaveBeenCalledTimes(2));
  });

  it("does not refetch on poll ticks while jobs are still running", async () => {
    // One job settles on the second poll, another keeps running so the
    // 1s active poll keeps ticking. Exactly one refetch may happen.
    let polls = 0;
    setUpApi(() => {
      polls += 1;
      return Promise.resolve([
        makeJob({ id: "job-a", status: polls === 1 ? "running" : "succeeded" }),
        makeJob({ id: "job-b", status: "running" }),
      ]);
    });
    renderShell();

    await waitFor(
      () => expect(api.getProject).toHaveBeenCalledTimes(2),
      { timeout: 4000 },
    );
    // Let two more active-poll ticks pass; the settled job must not
    // re-trigger, and steady running jobs must not trigger at all.
    await waitFor(() => expect(polls).toBeGreaterThanOrEqual(4), {
      timeout: 4000,
    });
    expect(api.getProject).toHaveBeenCalledTimes(2);
  });
});
