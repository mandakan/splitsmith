/**
 * The YouTube row on Export (#1000): the connection state comes from the
 * settings route, "Upload after render" reaches the request body as
 * ``youtube_upload`` + ``youtube_privacy``, and a history-row upload
 * submits the job with the same privacy.
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
      getYouTubeSettings: vi.fn(),
      uploadToYouTube: vi.fn(),
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


const CONNECTED = { configured: true, connected: true, channel_title: "Mine", connected_at: "2026-09-14T00:00:00Z" };

const UPLOADED_RUN = {
  run_id: "r1",
  kind: "match" as const,
  finished_at: "2026-09-14T12:00:00Z",
  duration_seconds: 30,
  stage_numbers: [1, 2],
  formats: ["mp4", "youtube-sidecar"],
  anomaly_count: 0,
  artifacts: [
    { filename: "bromma-2026.mp4", kind: "match_video", available: true },
    { filename: "bromma-2026-youtube.json", kind: "sidecar", available: true },
  ],
  youtube: null,
};

beforeEach(() => {
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
  vi.mocked(api.getExportOverview).mockResolvedValue(OVERVIEW);
  vi.mocked(api.getYouTubeSettings).mockResolvedValue(CONNECTED);
  vi.mocked(api.exportMatch).mockResolvedValue(job({ status: "running" }));
  vi.mocked(api.uploadToYouTube).mockResolvedValue(job({ status: "running", kind: "youtube_upload" }));
  vi.mocked(api.pollJob).mockImplementation(async (_id, onUpdate) => {
    const final = job({ status: "failed", error: "stopped by the test" });
    onUpdate?.(final);
    return final;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Export YouTube row", () => {
  it("sends youtube_upload off by default and the chosen privacy when set", async () => {
    const { user } = await renderPage();
    await user.selectOptions(screen.getByLabelText("Timeline format"), "mp4");
    await user.click(choice("YouTube", "Preset + sidecar"));
    await screen.findByText("Connected as Mine");
    await user.click(screen.getByRole("button", { name: /export bundle/i }));
    await waitFor(() => expect(api.exportMatch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.exportMatch).mock.calls[0][1]).toMatchObject({
      youtube_sidecar: true,
      youtube_upload: false,
      youtube_privacy: "unlisted",
      youtube_playlist: null,
      youtube_publish_at: null,
      youtube_notify_subscribers: true,
    });

    await user.click(choice("Upload after render", "Private"));
    await user.click(screen.getByLabelText("Add to playlist"));
    expect(await screen.findByLabelText("Playlist name")).toHaveValue("bromma-2026");
    await user.clear(screen.getByLabelText("Playlist name"));
    await user.type(screen.getByLabelText("Playlist name"), "Bromma 2026");
    await user.type(screen.getByLabelText("Publish at"), "2026-09-20T18:00");
    await user.click(screen.getByRole("button", { name: /export bundle/i }));
    await waitFor(() => expect(api.exportMatch).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.exportMatch).mock.calls[1][1]).toMatchObject({
      youtube_upload: true,
      youtube_privacy: "private",
      youtube_playlist: "Bromma 2026",
      youtube_publish_at: new Date("2026-09-20T18:00").toISOString(),
      youtube_notify_subscribers: true,
    });
  });

  it("uploads a history row with the form's privacy", async () => {
    vi.mocked(api.getExportRuns).mockResolvedValue({ runs: [UPLOADED_RUN] });
    const { user } = await renderPage();
    await user.click(await screen.findByRole("button", { name: "Upload to YouTube" }));
    await waitFor(() => expect(api.uploadToYouTube).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.uploadToYouTube).mock.calls[0]).toEqual([
      "mathias",
      {
        filename: "bromma-2026.mp4",
        again: false,
        privacy: "unlisted",
        playlist: null,
        publish_at: null,
        notify_subscribers: true,
      },
    ]);
  });

  it("hides the row when the install has no client and the export is not a YouTube mp4", async () => {
    vi.mocked(api.getYouTubeSettings).mockResolvedValue({ configured: false, connected: false, channel_title: null, connected_at: null });
    await renderPage();
    expect(screen.queryByText("Connected as Mine")).toBeNull();
    expect(screen.queryByRole("group", { name: "Upload after render" })).toBeNull();
  });
});
