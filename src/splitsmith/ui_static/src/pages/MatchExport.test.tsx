import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type Job, type MatchProject, type ShooterListEntry } from "@/lib/api";

import { MatchExport } from "@/pages/MatchExport";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn(),
      getProject: vi.fn(),
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
    competitor_name: null,
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
  };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    kind: "compare-grid",
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

function setUpLoad() {
  vi.mocked(api.listMatchShooters).mockResolvedValue({
    match_root: "/root",
    match_name: "Bromma Classic 2026",
    shooters: SHOOTERS,
    origin: "local",
  });
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
}

beforeEach(() => {
  setUpLoad();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("MatchExport", () => {
  it("pre-selects every non-skipped stage and defaults audio to the first shooter", async () => {
    render(<MatchExport />);

    expect(await screen.findByRole("radio", { name: /Mathias/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Casper/i })).not.toBeChecked();

    const stageOne = await screen.findByRole("button", { name: /Stage One/i });
    const stageTwo = screen.getByRole("button", { name: /Stage Two/i });
    const stageThree = screen.getByRole("button", { name: /Stage Three/i });

    // The stage buttons mount as soon as ``project`` loads, with
    // ``aria-pressed="false"``; the pre-select effect that flips eligible
    // stages to "true" is a separate state update landing after that
    // render. Asserting without a wait is a race (a real CI flake, #718).
    //
    // Both stages go inside the wait. They currently flip in the same
    // state update, so waiting on stageOne alone happens to cover
    // stageTwo -- but that is a coincidence of the current effect, not a
    // guarantee, and the whole point here is not to assert on timing that
    // holds by luck.
    await waitFor(() => {
      expect(stageOne).toHaveAttribute("aria-pressed", "true");
      expect(stageTwo).toHaveAttribute("aria-pressed", "true");
    });
    // Skipped stage: not selected and not clickable.
    expect(stageThree).toHaveAttribute("aria-pressed", "false");
    expect(stageThree).toBeDisabled();
  });

  it("submits the expected payload and shows a clean render as a success", async () => {
    const user = userEvent.setup();
    vi.mocked(api.exportCompareGrid).mockResolvedValue(makeJob({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(
      makeJob({
        status: "succeeded",
        result: {
          output_path: "/m/exports/compare-grid.mp4",
          stages_rendered: 2,
          stages_total: 2,
          failed: [],
        },
      }),
    );

    render(<MatchExport />);
    await screen.findByRole("radio", { name: /Mathias/i });

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

    const revealButton = screen.getByRole("button", { name: /reveal file/i });
    await user.click(revealButton);
    expect(api.revealFile).toHaveBeenCalledWith("/m/exports/compare-grid.mp4");
  });

  it("shows a partial render as a success with the failed stages named, never as a failure", async () => {
    const user = userEvent.setup();
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

    render(<MatchExport />);
    await screen.findByRole("radio", { name: /Mathias/i });
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    // The output is shown -- this is a success, not an error banner.
    expect(await screen.findByText(/rendered 1 of 2 stages/i)).toBeInTheDocument();
    expect(screen.getByText(/Stage Two did not render/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reveal file/i })).toBeInTheDocument();
    // A failed render (job.status === "failed") would show this text instead;
    // partial success must never be reported through that path.
    expect(screen.queryByText(/render failed/i)).not.toBeInTheDocument();
  });

  it("never reports a short render as a complete success", async () => {
    // Three stages requested, one of which nobody had a trim for: the
    // renderer never planned it, so the result is 2 of 3. Reading
    // "Rendered all 2 stages" under a green tick is the defect.
    const user = userEvent.setup();
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

    render(<MatchExport />);
    await screen.findByRole("radio", { name: /Mathias/i });
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    expect(await screen.findByText(/rendered 2 of 3 stages/i)).toBeInTheDocument();
    expect(screen.queryByText(/rendered all/i)).not.toBeInTheDocument();
    expect(screen.getByText(/stage 3 had no trim from any shooter/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Casper has no trim for stage 3 \(Stage Three\)/i),
    ).toBeInTheDocument();
  });

  it("deselecting a stage removes it from the render payload", async () => {
    const user = userEvent.setup();
    vi.mocked(api.exportCompareGrid).mockResolvedValue(makeJob({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(
      makeJob({
        status: "succeeded",
        result: {
          output_path: "/m/exports/compare-grid.mp4",
          stages_rendered: 1,
          stages_total: 1,
          failed: [],
        },
      }),
    );

    render(<MatchExport />);
    const stageTwo = await screen.findByRole("button", { name: /Stage Two/i });
    await user.click(stageTwo);
    expect(stageTwo).toHaveAttribute("aria-pressed", "false");

    await user.click(screen.getByRole("button", { name: /render grid/i }));

    await waitFor(() => expect(api.exportCompareGrid).toHaveBeenCalledTimes(1));
    expect(api.exportCompareGrid).toHaveBeenCalledWith(
      expect.objectContaining({ stage_numbers: [1] }),
    );
  });

  it("picking a different audio source changes audio_from in the payload", async () => {
    const user = userEvent.setup();
    vi.mocked(api.exportCompareGrid).mockResolvedValue(makeJob({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(makeJob({ status: "succeeded" }));

    render(<MatchExport />);
    await user.click(await screen.findByRole("radio", { name: /Casper/i }));
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    await waitFor(() => expect(api.exportCompareGrid).toHaveBeenCalledTimes(1));
    expect(api.exportCompareGrid).toHaveBeenCalledWith(
      expect.objectContaining({ audio_from: "casper" }),
    );
  });

  it("switching the canvas choice to 1080p changes the render dimensions", async () => {
    const user = userEvent.setup();
    vi.mocked(api.exportCompareGrid).mockResolvedValue(makeJob({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(makeJob({ status: "succeeded" }));

    render(<MatchExport />);
    await screen.findByRole("radio", { name: /Mathias/i });
    await user.selectOptions(screen.getByLabelText(/canvas/i), "hd");
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    await waitFor(() => expect(api.exportCompareGrid).toHaveBeenCalledTimes(1));
    expect(api.exportCompareGrid).toHaveBeenCalledWith(
      expect.objectContaining({ canvas_width: 1920, canvas_height: 1080 }),
    );
  });

  it("surfaces a submit-time rejection as an error without claiming success", async () => {
    const user = userEvent.setup();
    vi.mocked(api.exportCompareGrid).mockRejectedValue(
      new ApiError(400, "audio_from matches no shooter on this match"),
    );

    render(<MatchExport />);
    await screen.findByRole("radio", { name: /Mathias/i });
    await user.click(screen.getByRole("button", { name: /render grid/i }));

    expect(
      await screen.findByText(/audio_from matches no shooter on this match/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/rendered/i)).not.toBeInTheDocument();
  });
});
