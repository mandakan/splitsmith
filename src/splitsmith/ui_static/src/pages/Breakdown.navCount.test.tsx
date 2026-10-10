/**
 * Breakdown inside the real match shell (#1371): a region save refreshes
 * the shell's project, so the nav row's quiet count follows Keep and
 * disappears with the last region, without a reload or a job.
 */
import { useMemo, useState, type ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { ShellChromeProvider, type ShellChromeValue } from "@/components/layout/shellChromeContext";
import { MatchShell } from "@/components/match/MatchShell";
import type { CoachStageResponse, MatchProject, StageEvent } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import { Breakdown } from "@/pages/Breakdown";

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
      getTriageSummary: vi.fn(),
      listJobs: vi.fn(),
      getStageCoach: vi.fn(),
      getMatchCoachDistributions: vi.fn().mockResolvedValue(null),
      getStagePeaks: vi.fn().mockRejectedValue(new Error("no peaks here")),
      putStageEvents: vi.fn(),
      getScrubSettings: vi.fn().mockResolvedValue({ full_res_scrub: false }),
      videoStreamUrl: () => "http://localhost/v.mp4",
    },
  };
});

vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => false }));

import { api } from "@/lib/api";

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
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
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});

function ShellChromeHarness({ children }: { children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const value = useMemo<ShellChromeValue>(
    () => ({ contextSlot: slot, stripSlot: null, crumbSlot: null, setAccent: () => {}, setOwnsMobileAccount: () => {} }),
    [slot],
  );
  return (
    <ShellChromeProvider value={value}>
      <div ref={setSlot} />
      {children}
    </ShellChromeProvider>
  );
}

/** The confirmed-region count the server reports on ``figures.regions``. */
let serverRegions: number | null = null;

function project(): MatchProject {
  return {
    schema_version: 1,
    name: "bromma-2026",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    competitor_name: "Mathias",
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
        figures: { draw: 1, avg_split: 0.3, fastest_split: 0.2, shot_count: 2, split_count: 1, regions: serverRegions },
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
    origin: "local",
    capabilities: ["edit", "review"],
  } as MatchProject;
}

function coach(events: StageEvent[], version: string): CoachStageResponse {
  return {
    stage_number: 1,
    stage_name: "Stage One",
    beep_time: 5,
    version: 1,
    videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5, kind: "source" }],
    shots: [1, 2].map((n) => ({
      id: `c${n}`,
      shot_number: n,
      ms_after_beep: n * 1000,
      time_from_beep: n,
      time_absolute: 5 + n,
      split: 0.3,
      interval_class: "split" as const,
      interval_class_source: "auto" as const,
      improvement_flag: false,
      coaching_note: null,
      stale: false,
      reload_hint: false,
    })),
    events,
    _version: version,
  };
}

const PROPOSAL: StageEvent = { id: "evt-1", kind: "reload", start: 2.2, end: 3.4, source: "auto" };

function setUpApi() {
  vi.mocked(api.getHealth).mockResolvedValue({
    status: "ok",
    version: "0.0.0-test",
    bound: false,
    project_name: "bromma-2026",
    project_root: "/root/bromma-2026",
    match_id: "m1",
    kind: "match",
    default_shooter_slug: "mathias",
    schema_version: 1,
  });
  vi.mocked(api.getScoreboardIdentity).mockResolvedValue(null);
  vi.mocked(api.getServerFeatures).mockResolvedValue({ lab: false, mode: "local" });
  vi.mocked(api.getMe).mockResolvedValue({
    id: "local",
    email: "local@localhost",
    display_name: null,
    is_admin: false,
    access_tier: "full",
    features: ["create_match", "hosted_compute", "raw_upload", "share", "sync"],
  });
  vi.mocked(api.listMatchShooters).mockResolvedValue({
    match_root: "/root",
    match_name: "Bromma",
    shooters: [
      {
        slug: "mathias",
        name: "Mathias",
        selected_shooter_id: null,
        selected_competitor_id: null,
        stages_audited: 1,
        stages_total: 1,
        video_count: 1,
        cameras: [],
        stages_missing_trim: 0,
        stage_statuses: [],
      },
    ],
    origin: "local",
    capabilities: ["edit", "review"],
  });
  vi.mocked(api.getProject).mockImplementation(async () => project());
  vi.mocked(api.getBeepQueue).mockResolvedValue({
    total_items: 0,
    pending_count: 0,
    confirmed_count: 0,
    stages: [],
    origin: "local",
    capabilities: ["edit", "review"],
  });
  vi.mocked(api.getTriageSummary).mockResolvedValue({ flagged_count: 0 });
  vi.mocked(api.listJobs).mockResolvedValue([]);
  vi.mocked(api.getStageCoach).mockResolvedValue(coach([PROPOSAL], "aaaaaaaaaaaaaaaa"));
}

describe("the Breakdown nav count follows region saves", () => {
  it("appears after Keep and goes away when the last region is deleted", async () => {
    serverRegions = null;
    setUpApi();
    render(
      <ModeProvider>
        <AuthProvider>
          <ShellChromeHarness>
            <MemoryRouter initialEntries={["/breakdown/mathias/1"]}>
              <Routes>
                <Route element={<MatchShell />}>
                  <Route path="/breakdown/:slug/:stage" element={<Breakdown />} />
                </Route>
              </Routes>
            </MemoryRouter>
          </ShellChromeHarness>
        </AuthProvider>
      </ModeProvider>,
    );

    fireEvent.click(await screen.findByTestId("event-evt-1"));
    expect(screen.queryByLabelText(/in this match/)).toBeNull();

    // Keep confirms the proposal; the server now counts one region.
    const kept = { ...PROPOSAL, source: "manual" as const };
    vi.mocked(api.putStageEvents).mockImplementationOnce(async () => {
      serverRegions = 1;
      return coach([kept], "bbbbbbbbbbbbbbbb");
    });
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(await screen.findByLabelText("1 region in this match", {}, { timeout: 3000 })).toHaveTextContent("1");

    // Deleting the last region: the count goes, nothing at zero.
    vi.mocked(api.putStageEvents).mockImplementationOnce(async () => {
      serverRegions = null;
      return coach([], "cccccccccccccccc");
    });
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByLabelText(/in this match/)).toBeNull(), { timeout: 3000 });
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
  });
});
