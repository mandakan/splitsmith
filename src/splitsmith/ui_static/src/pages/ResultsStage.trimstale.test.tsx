/**
 * Task 9 (mobile beep review slice 3): the "Awaiting desktop re-process"
 * chip on ResultsStage. A phone beep edit on a mirror sets beep_time but
 * leaves processed.trim false until the next desktop sync re-derives the
 * trimmed clip - this surfaces that gap next to the stage heading.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import type {
  CoachStageResponse,
  MatchProject,
  ShooterListEntry,
  StageEntry,
  StageVideo,
} from "@/lib/api";

import { ResultsStage } from "@/pages/ResultsStage";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getStageCoach: vi.fn(),
      getProject: vi.fn(),
      getMatchCoachDistributions: vi.fn().mockRejectedValue(new Error("no dist")),
      videoStreamUrl: () => "http://localhost/video.mp4",
    },
  };
});

import { api } from "@/lib/api";

beforeAll(() => {
  // jsdom lacks both; ResultsStage measures the player box and the
  // ShotTicker probes prefers-reduced-motion.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  window.matchMedia = ((query: string) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

function makeCoach(): CoachStageResponse {
  return {
    stage_number: 3,
    stage_name: "Steel Rush",
    beep_time: 2,
    videos: [{ path: "trimmed/stage3.mp4", role: "primary", beep_in_clip: 2 }],
    shots: [],
  };
}

function makeVideo(overrides: Partial<StageVideo> = {}): StageVideo {
  return {
    path: "trimmed/stage3.mp4",
    video_id: "v1",
    role: "primary",
    added_at: "2026-01-01T00:00:00Z",
    match_timestamp: null,
    processed: { beep: true, shot_detect: false, trim: false },
    beep_time: 2.0,
    beep_source: "auto",
    beep_reviewed: false,
    beep_peak_amplitude: null,
    beep_duration_ms: null,
    beep_confidence: null,
    beep_candidates: [],
    beep_auto_detect_failed: false,
    beep_alignment_confidence: null,
    beep_alignment_delta_ms: null,
    notes: "",
    camera_mount: null,
    camera_make: null,
    camera_model: null,
    ...overrides,
  };
}

function makeStageEntry(videos: StageVideo[]): StageEntry {
  return {
    stage_number: 3,
    stage_name: "Steel Rush",
    time_seconds: 20,
    scorecard_updated_at: null,
    videos,
    skipped: false,
    placeholder: false,
    time_seconds_manual: false,
    stage_rounds: null,
    scorecard: null,
  };
}

function makeProject(videos: StageVideo[]): MatchProject {
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
    stages: [makeStageEntry(videos)],
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
  };
}

function makeShooter(slug: string, name: string): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: 1,
    stages_total: 1,
    video_count: 1,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: [{ stage_number: 3, status: "audited" }],
  };
}

function Shell({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderStage(videos: StageVideo[]) {
  vi.mocked(api.getStageCoach).mockResolvedValue(makeCoach());
  vi.mocked(api.getProject).mockResolvedValue(makeProject(videos));
  const ctx: MatchShellOutletContext = {
    project: null,
    health: null,
    shooters: [makeShooter("anna", "Anna")],
    refresh: vi.fn(),
    origin: null,
  };
  return render(
    <MemoryRouter initialEntries={["/match/m1/results/anna/3"]}>
      <Routes>
        <Route element={<Shell ctx={ctx} />}>
          <Route path="/match/:matchId/results/:slug/:stage" element={<ResultsStage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("ResultsStage staleness chip", () => {
  it("shows the awaiting-desktop-reprocess chip when a video's trim is stale", async () => {
    renderStage([makeVideo({ beep_time: 2.0, processed: { beep: true, trim: false, shot_detect: false } })]);
    expect(await screen.findByText(/awaiting desktop re-process/i)).toBeInTheDocument();
  });

  it("hides the chip once the video has been re-trimmed", async () => {
    renderStage([makeVideo({ beep_time: 2.0, processed: { beep: true, trim: true, shot_detect: false } })]);
    // Wait for a value that only appears once the project fetch resolves,
    // so the negative assertion below isn't just racing a still-loading page.
    await screen.findByText(/steel rush/i);
    expect(screen.queryByText(/awaiting desktop re-process/i)).not.toBeInTheDocument();
  });

  it("hides the chip when the only stale video has role ignored", async () => {
    renderStage([
      makeVideo({
        role: "ignored",
        beep_time: 2.0,
        processed: { beep: true, trim: false, shot_detect: false },
      }),
    ]);
    await screen.findByText(/steel rush/i);
    expect(screen.queryByText(/awaiting desktop re-process/i)).not.toBeInTheDocument();
  });

  it("hides the chip when a video has beep_time null and trim false", async () => {
    renderStage([makeVideo({ beep_time: null, processed: { beep: true, trim: false, shot_detect: false } })]);
    await screen.findByText(/steel rush/i);
    expect(screen.queryByText(/awaiting desktop re-process/i)).not.toBeInTheDocument();
  });
});
