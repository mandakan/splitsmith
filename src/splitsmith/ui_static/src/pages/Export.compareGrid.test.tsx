/**
 * The compare grid as Export's third output mode (UX PR 8; the cases are
 * the retired MatchExport page's). The mode exists only on a multi-
 * shooter match; the reference shooter and canvas are segmented rows;
 * a partial render is a success with the failed stages named, never a
 * failure; a short render is never reported as complete.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  type ExportOverview,
  type Job,
  type MatchProject,
  type ShooterListEntry,
  type StageExportStatus,
} from "@/lib/api";
import { ConfirmProvider } from "@/components/useConfirm";
import { Export } from "@/pages/Export";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
      getProject: vi.fn(),
      getExportOverview: vi.fn(),
      getExportRuns: vi.fn().mockResolvedValue({ runs: [] }),
      getCleanupPlan: vi.fn().mockResolvedValue({ items: [], totals_by_category: {}, total_bytes: 0, total_file_count: 0 }),
      exportCompareGrid: vi.fn(),
      pollJob: vi.fn(),
      revealFile: vi.fn(),
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
    stages_total: 2,
    video_count: 0,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: [],
  };
}

function makeStage(n: number, name: string, skipped = false): MatchProject["stages"][number] {
  return {
    stage_number: n,
    stage_name: name,
    time_seconds: 20,
    scorecard_updated_at: null,
    videos: [],
    skipped,
    placeholder: false,
    time_seconds_manual: false,
    stage_rounds: null,
    scorecard: null,
  };
}

function makeProject(stages: MatchProject["stages"]): MatchProject {
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
    stages,
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

function overviewStage(n: number, name: string, skipped = false): StageExportStatus {
  return {
    stage_number: n,
    stage_name: name,
    skipped,
    has_primary: false,
    primary_processed: { beep: false, shot_detect: false, trim: false },
    audit_shot_count: 0,
    total_candidate_count: 0,
    audit_path: null,
    trimmed_video_path: null,
    lossless_trim_present: false,
    csv_path: null,
    fcpxml_path: null,
    report_path: null,
    overlay_path: null,
    has_exports: false,
    last_export_at: null,
    ready_to_export: false,
    ready_to_trim: false,
    ready_to_export_bare: false,
    source_reachable: null,
    secondaries: [],
  };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    kind: "compare-grid",
    match_id: "m1",
    stage_number: null,
    shooter_slug: null,
    video_id: null,
    status: "succeeded",
    progress: 1,
    message: null,
    error: null,
    cancel_requested: false,
    acknowledged: false,
    result: null,
    timings: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const SHOOTERS = [makeShooter("mathias", "Mathias"), makeShooter("casper", "Casper")];
const PROJECT = makeProject([
  makeStage(1, "Stage One"),
  makeStage(2, "Stage Two"),
  makeStage(3, "Stage Three", true), // skipped -- must not default-select
]);
const OVERVIEW: ExportOverview = {
  match_exports: [],
  stages: [overviewStage(1, "Stage One"), overviewStage(2, "Stage Two"), overviewStage(3, "Stage Three", true)],
};

function Shell({ shooters }: { shooters: ShooterListEntry[] }) {
  return (
    <Outlet
      context={{
        project: PROJECT,
        health: { project_root: "/m/bromma" },
        shooters,
        refresh: () => {},
        origin: "local",
        capabilities: ["edit", "review"],
      }}
    />
  );
}

async function renderCompare(shooters = SHOOTERS) {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/match/m1/export/mathias"]}>
      <ConfirmProvider>
        <Routes>
          <Route path="/match/:matchId" element={<Shell shooters={shooters} />}>
            <Route path="export/:slug" element={<Export />} />
          </Route>
        </Routes>
      </ConfirmProvider>
    </MemoryRouter>,
  );
  const grid = await screen.findByRole("button", { name: /compare grid/i });
  return { user, grid };
}

beforeEach(() => {
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
  vi.mocked(api.getExportOverview).mockResolvedValue(OVERVIEW);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Export compare grid mode", () => {
  it("is offered only on a multi-shooter match", async () => {
    const { grid } = await renderCompare([SHOOTERS[0]]);
    expect(grid).toBeDisabled();
    expect(grid).toHaveAttribute("title", expect.stringMatching(/two or more shooters/i));
  });

  it("pre-selects every non-skipped stage and defaults the reference to the first shooter", async () => {
    const { user, grid } = await renderCompare();
    await user.click(grid);
    expect(screen.getByRole("button", { name: /^Mathias$/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Casper$/ })).toHaveAttribute("aria-pressed", "false");
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /Stage One/i })).toBeChecked();
      expect(screen.getByRole("checkbox", { name: /Stage Two/i })).toBeChecked();
    });
    const three = screen.getByRole("checkbox", { name: /Stage Three/i });
    expect(three).not.toBeChecked();
    expect(three).toBeDisabled();
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });

  it("submits the expected payload and shows a clean render as a success", async () => {
    vi.mocked(api.exportCompareGrid).mockResolvedValue(makeJob({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(
      makeJob({
        status: "succeeded",
        result: { output_path: "/m/exports/compare-grid.mp4", stages_rendered: 2, stages_total: 2, failed: [] },
      }),
    );
    const { user, grid } = await renderCompare();
    await user.click(grid);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Stage Two/i })).toBeChecked());
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    await waitFor(() => expect(api.exportCompareGrid).toHaveBeenCalledTimes(1));
    expect(api.exportCompareGrid).toHaveBeenCalledWith({
      stage_numbers: [1, 2],
      audio_from: "mathias",
      canvas_width: 3840,
      canvas_height: 2160,
      output_name: "compare-grid",
    });
    expect(await screen.findByText(/rendered all 2 stages/i)).toBeInTheDocument();
    expect(screen.queryByText(/did not render/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /reveal file/i }));
    expect(api.revealFile).toHaveBeenCalledWith("/m/exports/compare-grid.mp4");
  });

  it("shows a partial render as a success with the failed stages named, never as a failure", async () => {
    vi.mocked(api.exportCompareGrid).mockResolvedValue(makeJob({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(
      makeJob({
        status: "succeeded",
        result: {
          output_path: "/m/exports/compare-grid.mp4",
          stages_rendered: 1,
          stages_total: 2,
          failed: [{ stage_number: 2, stage_name: "Stage Two", error: "ffmpeg exit 1" }],
        },
      }),
    );
    const { user, grid } = await renderCompare();
    await user.click(grid);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Stage Two/i })).toBeChecked());
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    expect(await screen.findByText(/rendered 1 of 2 stages/i)).toBeInTheDocument();
    expect(screen.getByText(/Stage Two did not render/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reveal file/i })).toBeInTheDocument();
    expect(screen.queryByText(/render failed/i)).not.toBeInTheDocument();
  });

  it("never reports a short render as a complete success", async () => {
    vi.mocked(api.exportCompareGrid).mockResolvedValue(makeJob({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(
      makeJob({
        status: "succeeded",
        result: {
          output_path: "/m/exports/compare-grid.mp4",
          stages_rendered: 2,
          stages_total: 3,
          failed: [],
          skipped_stages: [3],
          missing_trims: [
            {
              shooter: "Casper",
              stage_number: 3,
              stage_name: "Stage Three",
              expected_path: "/m/casper/exports/stage3_stage-three_trimmed.mp4",
              camera: null,
            },
          ],
        },
      }),
    );
    const { user, grid } = await renderCompare();
    await user.click(grid);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Stage Two/i })).toBeChecked());
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    expect(await screen.findByText(/rendered 2 of 3 stages/i)).toBeInTheDocument();
    expect(screen.queryByText(/rendered all/i)).not.toBeInTheDocument();
    expect(screen.getByText(/stage 3 had no trim from any shooter/i)).toBeInTheDocument();
    expect(screen.getByText(/Casper has no trim for stage 3 \(Stage Three\)/i)).toBeInTheDocument();
  });

  it("deselecting a stage removes it from the render payload", async () => {
    vi.mocked(api.exportCompareGrid).mockResolvedValue(makeJob({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(
      makeJob({
        status: "succeeded",
        result: { output_path: "/m/exports/compare-grid.mp4", stages_rendered: 1, stages_total: 1, failed: [] },
      }),
    );
    const { user, grid } = await renderCompare();
    await user.click(grid);
    const two = screen.getByRole("checkbox", { name: /Stage Two/i });
    await waitFor(() => expect(two).toBeChecked());
    await user.click(two);
    expect(two).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    await waitFor(() => expect(api.exportCompareGrid).toHaveBeenCalledTimes(1));
    expect(api.exportCompareGrid).toHaveBeenCalledWith(expect.objectContaining({ stage_numbers: [1] }));
  });

  it("picking a different reference shooter changes audio_from; 1080p changes the canvas", async () => {
    vi.mocked(api.exportCompareGrid).mockResolvedValue(makeJob({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(makeJob({ status: "succeeded" }));
    const { user, grid } = await renderCompare();
    await user.click(grid);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Stage Two/i })).toBeChecked());
    await user.click(screen.getByRole("button", { name: /^Casper$/ }));
    await user.click(screen.getByRole("button", { name: /1080p/i }));
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    await waitFor(() => expect(api.exportCompareGrid).toHaveBeenCalledTimes(1));
    expect(api.exportCompareGrid).toHaveBeenCalledWith(
      expect.objectContaining({ audio_from: "casper", canvas_width: 1920, canvas_height: 1080 }),
    );
  });

  it("surfaces a submit-time rejection as an error without claiming success", async () => {
    vi.mocked(api.exportCompareGrid).mockRejectedValue(new ApiError(400, "audio_from matches no shooter on this match"));
    const { user, grid } = await renderCompare();
    await user.click(grid);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Stage Two/i })).toBeChecked());
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    expect(await screen.findByText(/audio_from matches no shooter on this match/i)).toBeInTheDocument();
    expect(screen.queryByText(/rendered/i)).not.toBeInTheDocument();
  });
});
