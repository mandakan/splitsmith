/**
 * A stage with a reviewed beep and a time but no audited shots exports in
 * bundle mode, marked "No splits" on its row and counted in the rail;
 * a stage whose beep is not reviewed stays blocked with the audit as fix.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { api, type Job, type MatchProject, type ShooterListEntry, type StageExportStatus } from "@/lib/api";
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
      getYouTubeSettings: vi.fn().mockRejectedValue(new Error("no youtube in this test")),
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
    ready_to_export_bare: true,
    source_reachable: true,
    secondaries: [],
  };
}


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

/** The option groups fold by default (spec 2026-09-15 s1); open whichever exist. */
async function openGroups(user: ReturnType<typeof userEvent.setup>) {
  for (const name of ["Output", "Cut", "Look"]) {
    const btn = screen.queryByRole("button", { name: new RegExp(`^${name}$`), expanded: false });
    if (btn) await user.click(btn);
  }
}

/** A gallery tile: the Look group's radio per slot (spec 2026-09-15 s2). */
function tile(slot: string, name: string): HTMLElement {
  return within(screen.getByRole("radiogroup", { name: slot })).getByRole("radio", { name });
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
  await waitFor(() => expect(screen.getByRole("checkbox", { name: /Stage 1/i })).toBeChecked());
  await openGroups(user);
  return { user };
}




function bare(n: number): StageExportStatus {
  return {
    ...ready(n),
    primary_processed: { beep: true, shot_detect: false, trim: false },
    audit_shot_count: 0,
    total_candidate_count: 0,
    ready_to_export: false,
    ready_to_export_bare: true,
  };
}

function unreviewed(n: number): StageExportStatus {
  return { ...bare(n), ready_to_export_bare: false };
}

beforeEach(() => {
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
  vi.mocked(api.exportMatch).mockResolvedValue(job({ status: "running" }));
  vi.mocked(api.pollJob).mockImplementation(async (_id, onUpdate) => {
    const final = job({ status: "failed", error: "stopped by the test" });
    onUpdate?.(final);
    return final;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Export without splits", () => {
  it("selects a bare stage, says so on the row and in the rail, and exports it", async () => {
    vi.mocked(api.getExportOverview).mockResolvedValue({ match_exports: [], stages: [ready(1), bare(2)] });
    const { user } = await renderPage();
    const row2 = screen.getByRole("checkbox", { name: /Stage 2/i });
    expect(row2).toBeChecked();
    expect(screen.getByText("No splits")).toBeInTheDocument();
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByText("1 stage without")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /export bundle/i }));
    await waitFor(() => expect(api.exportMatch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.exportMatch).mock.calls[0][1].stage_numbers).toEqual([1, 2]);
  });

  it("keeps an unreviewed beep blocked with the audit as the fix", async () => {
    vi.mocked(api.getExportOverview).mockResolvedValue({ match_exports: [], stages: [ready(1), unreviewed(2)] });
    await renderPage();
    expect(screen.getByRole("checkbox", { name: /Stage 2/i })).toBeDisabled();
    expect(screen.getByText("No confirmed beep")).toBeInTheDocument();
    expect(screen.queryByText("No splits")).toBeNull();
    expect(screen.getByText("1 / 1")).toBeInTheDocument();
  });
});

describe("Export bare-stage hints", () => {
  it("each shot-dependent option says what it loses, only while a bare stage is selected and it is on", async () => {
    vi.mocked(api.getExportOverview).mockResolvedValue({ match_exports: [], stages: [ready(1), bare(2)] });
    const { user } = await renderPage();
    // Overlay off: no hint.
    expect(screen.queryByText(/Skipped on 1 stage without splits/)).toBeNull();
    await user.click(tile("Overlay", "Shot counter"));
    expect(screen.getByText(/Skipped on 1 stage without splits/)).toBeInTheDocument();
    // MP4 unlocks the summary hold and the YouTube kit.
    await user.selectOptions(screen.getByLabelText("Timeline format"), "mp4");
    await user.click(tile("Stage summary", "Summary hold"));
    await user.clear(screen.getByLabelText("Summary hold seconds"));
    await user.type(screen.getByLabelText("Summary hold seconds"), "2");
    expect(screen.getByText(/Time and scoring only on 1 stage without splits/)).toBeInTheDocument();
    await user.click(within(screen.getByRole("group", { name: "YouTube" })).getByRole("button", { name: "Preset + sidecar" }));
    expect(screen.getByText(/Captions cover the audited stages only; 1 stage has none/)).toBeInTheDocument();
    // Deselect the bare stage: every hint goes.
    await user.click(screen.getByRole("checkbox", { name: /Stage 2/i }));
    expect(screen.queryByText(/without splits/)).toBeNull();
    expect(screen.queryByText(/Captions cover/)).toBeNull();
  });
});
