/**
 * The rendered-video rows on Export (#973, #972, #974): the card panel,
 * the cam rows and the YouTube toggle reach the request bodies, and
 * only what the chosen format can draw travels. The grid takes the same
 * card state plus its own overlay and hold (#705).
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { api, type ExportOverview, type Job, type MatchProject, type ShooterListEntry, type StageExportStatus } from "@/lib/api";
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
      exportMatch: vi.fn(),
      exportCompareGrid: vi.fn(),
      pollJob: vi.fn(),
      revealFile: vi.fn(),
    },
  };
});

function shooter(slug: string, name: string): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: 2,
    stages_total: 2,
    video_count: 0,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: [],
  };
}

function video(id: string, role: "primary" | "secondary", beep: number | null): MatchProject["stages"][number]["videos"][number] {
  return {
    video_id: id,
    path: `raw/${id}.mp4`,
    role,
    beep_time: beep,
    beep_reviewed: beep !== null,
    beep_source: beep === null ? null : "auto",
    beep_candidates: [],
    processed: { beep: beep !== null, shot_detect: false, trim: false },
    camera_mount: null,
    match_score: null,
  } as unknown as MatchProject["stages"][number]["videos"][number];
}

function stage(n: number, videos: MatchProject["stages"][number]["videos"]): MatchProject["stages"][number] {
  return {
    stage_number: n,
    stage_name: `Stage ${n}`,
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

const PROJECT: MatchProject = {
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
    stage(1, [video("p1", "primary", 5), video("c1", "secondary", 5.2)]),
    stage(2, [video("p2", "primary", 5), video("c2", "secondary", null)]),
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
};

function ready(n: number): StageExportStatus {
  return {
    stage_number: n,
    stage_name: `Stage ${n}`,
    skipped: false,
    has_primary: true,
    primary_processed: { beep: true, shot_detect: true, trim: true },
    audit_shot_count: 8,
    total_candidate_count: 8,
    audit_path: null,
    trimmed_video_path: null,
    lossless_trim_present: false,
    csv_path: null,
    fcpxml_path: null,
    report_path: null,
    overlay_path: null,
    has_exports: false,
    last_export_at: null,
    ready_to_export: true,
    ready_to_trim: true,
    source_reachable: true,
    secondaries: [],
  };
}

const OVERVIEW: ExportOverview = { match_exports: [], stages: [ready(1), ready(2)] };

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    kind: "export",
    match_id: "m1",
    stage_number: null,
    shooter_slug: "mathias",
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

async function renderPage(shooters = [shooter("mathias", "Mathias")]) {
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
  await screen.findByRole("button", { name: /export bundle/i });
  await waitFor(() => expect(screen.getByRole("checkbox", { name: /Stage 2/i })).toBeChecked());
  return { user };
}

function choice(group: string, label: string): HTMLElement {
  return within(screen.getByRole("group", { name: group })).getByRole("button", { name: label });
}

beforeEach(() => {
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
  vi.mocked(api.getExportOverview).mockResolvedValue(OVERVIEW);
  vi.mocked(api.exportMatch).mockResolvedValue(job({ status: "running" }));
  vi.mocked(api.exportCompareGrid).mockResolvedValue(job({ status: "running", kind: "compare-grid" }));
  // The real poller reports every update, the final one included, so the
  // page leaves its busy state; the mock has to do the same.
  vi.mocked(api.pollJob).mockImplementation(async (_id, onUpdate) => {
    const final = job({ status: "failed", error: "stopped by the test" });
    onUpdate?.(final);
    return final;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Export rendered-video rows", () => {
  it("on FCPXML sends the stage card and the cams, no match card, no YouTube", async () => {
    const { user } = await renderPage();
    // One synced secondary across the two stages: the cam rows show.
    expect(screen.getByText("1 synced camera")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "YouTube" })).toBeNull();
    expect(choice("Title page", "Opening")).toBeDisabled();
    await user.click(choice("Stage card style", "Slate"));
    await user.click(choice("Secondary cam layout", "Picture-in-picture"));
    await user.click(screen.getByRole("button", { name: /export bundle/i }));

    await waitFor(() => expect(api.exportMatch).toHaveBeenCalledTimes(1));
    const body = vi.mocked(api.exportMatch).mock.calls[0][1];
    expect(body).toMatchObject({
      output_format: "fcpxml",
      title_kind: "slate",
      title_duration_seconds: 1.5,
      include_secondaries: true,
      pip_layout: "pip-corners",
      youtube_sidecar: false,
      youtube_preset: false,
      youtube_upload: false,
      youtube_privacy: "unlisted",
      youtube_playlist: null,
      youtube_publish_at: null,
      youtube_notify_subscribers: true,
    });
    expect("title_page" in body).toBe(false);
    expect("summary_hold_seconds" in body).toBe(false);
    expect(body.description_lead).toBeUndefined();
  });

  it("on MP4 sends the title page, the summary hold and the YouTube pair, and lists the sidecar files", async () => {
    const { user } = await renderPage();
    await user.selectOptions(screen.getByLabelText("Timeline format"), "mp4");
    await user.click(choice("Title page", "Opening + closing"));
    await user.type(screen.getByLabelText("Title page info line"), "Production Optics");
    await user.clear(screen.getByLabelText("Summary hold seconds"));
    await user.type(screen.getByLabelText("Summary hold seconds"), "3");
    await user.click(choice("YouTube", "Preset + sidecar"));
    await user.type(screen.getByLabelText("Description lead"), "  Production Optics, head cam  ");
    await user.click(choice("Secondary cams", "Primary only"));

    expect(screen.getByText("bromma-2026-youtube.json")).toBeInTheDocument();
    expect(screen.getByText("bromma-2026.srt")).toBeInTheDocument();
    expect(screen.getByText("title page · summary 3 s · closing")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /export bundle/i }));
    await waitFor(() => expect(api.exportMatch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.exportMatch).mock.calls[0][1]).toMatchObject({
      output_format: "mp4",
      title_page: true,
      title_info: "Production Optics",
      title_page_duration_seconds: 3,
      closing_card: true,
      summary_hold_seconds: 3,
      title_kind: "none",
      include_secondaries: false,
      youtube_sidecar: true,
      youtube_preset: true,
      youtube_upload: false,
      youtube_privacy: "unlisted",
      youtube_playlist: null,
      youtube_publish_at: null,
      youtube_notify_subscribers: true,
      description_lead: "Production Optics, head cam",
    });
  });

  it("the grid takes the same cards plus its own overlay and hold, and nothing when untouched", async () => {
    const { user } = await renderPage([shooter("mathias", "Mathias"), shooter("casper", "Casper")]);
    await user.click(screen.getByRole("button", { name: /compare grid/i }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Stage 2/i })).toBeChecked());
    expect(screen.queryByLabelText("Summary hold seconds")).toBeNull();
    await user.click(screen.getByRole("button", { name: /render grid/i }));
    await waitFor(() => expect(api.exportCompareGrid).toHaveBeenCalledTimes(1));
    const untouched = vi.mocked(api.exportCompareGrid).mock.calls[0][0];
    expect("title_page" in untouched).toBe(false);
    expect("overlay" in untouched).toBe(false);

    await user.click(choice("Title page", "Opening"));
    await user.click(choice("Grid overlay", "Counter + splits"));
    await user.clear(screen.getByLabelText("Grid summary hold seconds"));
    await user.type(screen.getByLabelText("Grid summary hold seconds"), "2");
    await user.click(screen.getByRole("button", { name: /render grid/i }));
    await waitFor(() => expect(api.exportCompareGrid).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.exportCompareGrid).mock.calls[1][0]).toMatchObject({
      title_page: true,
      stage_titles: "none",
      overlay: true,
      summary_hold_seconds: 2,
    });
  });
});
