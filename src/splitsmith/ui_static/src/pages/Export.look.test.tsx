/**
 * The Look group fed from the Looks API (#1246), through the page: the
 * Look tiles, the Style under a card, the Look's sting as a transition
 * tile, and the choice reaching the export body, the rail and the
 * preview request. Fixtures mirror Export.renderOptions.test.tsx.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { api, type ExportOverview, type Job, type LookInfo, type MatchProject, type ShooterListEntry, type StageExportStatus } from "@/lib/api";
import { resetLooksForTests } from "@/lib/useLooks";
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
      exportPreview: vi.fn(),
      listLooks: vi.fn(),
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

/** The option groups fold by default (spec 2026-09-15 s1); open whichever exist. */
async function openGroups(user: ReturnType<typeof userEvent.setup>) {
  for (const name of ["Output", "Cut", "Look"]) {
    const btn = screen.queryByRole("button", { name: new RegExp(`^${name}$`), expanded: false });
    if (btn) await user.click(btn);
  }
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
  await openGroups(user);
  return { user };
}

/** A gallery tile: the Look group's radio per slot (spec 2026-09-15 s2). */
function tile(slot: string, name: string): HTMLElement {
  return within(screen.getByRole("radiogroup", { name: slot })).getByRole("radio", { name });
}

function choice(group: string, label: string): HTMLElement {
  return within(screen.getByRole("group", { name: group })).getByRole("button", { name: label });
}

const LOOKS: LookInfo[] = [
  {
    name: "splitsmith",
    label: "Splitsmith",
    source: "shipped",
    accent_series: [],
    preview: "/api/looks/splitsmith/preview/look.png",
    slots: {
      title_page: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      slate: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      lower_third: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      summary: [],
      closing: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      transition: [{ name: "wipe", preview: "/api/looks/splitsmith/preview/transition-wipe.webp" }],
    },
  },
  {
    name: "clean",
    label: "Clean",
    source: "shipped",
    accent_series: [],
    preview: "/api/looks/clean/preview/look.png",
    slots: {
      title_page: [{ name: "default", preview: null }],
      slate: [{ name: "default", preview: null }],
      lower_third: [{ name: "default", preview: null }],
      summary: [],
      closing: [{ name: "default", preview: null }],
      transition: [],
    },
  },
];


beforeEach(() => {
  resetLooksForTests();
  vi.mocked(api.listLooks).mockResolvedValue({ looks: LOOKS });
  vi.mocked(api.exportPreview).mockResolvedValue(new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" }));
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

describe("Export's Look group from the catalog", () => {
  it("offers the installed Looks and a Style, and sends the choice, the sting and the rail line", async () => {
    const { user } = await renderPage();
    await user.selectOptions(screen.getByLabelText("Timeline format"), "mp4");
    await waitFor(() => expect(screen.getByRole("radiogroup", { name: "Look" })).toBeInTheDocument());
    expect(within(screen.getByRole("radiogroup", { name: "Look" })).getAllByRole("radio").map((r) => r.textContent)).toEqual([
      "Splitsmith",
      "Clean",
    ]);
    await user.click(tile("Title page", "Title page"));
    await user.click(choice("Title page style", "Rise"));
    await user.click(tile("Transition", "Wipe"));
    // The rail names the sting, not a cut (review of #1246).
    expect(screen.getByText(/sting:wipe 0\.5 s/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /export bundle/i }));
    await waitFor(() => expect(api.exportMatch).toHaveBeenCalledTimes(1));
    const body = vi.mocked(api.exportMatch).mock.calls[0][1];
    expect(body).toMatchObject({ output_format: "mp4", title_page: true, title_page_variant: "rise", transition_kind: "sting:wipe" });
    expect("overlay_theme" in body).toBe(false);
  });

  it("the Clean Look drops the variant it lacks and the sting, and reaches the preview request", async () => {
    const { user } = await renderPage();
    await user.selectOptions(screen.getByLabelText("Timeline format"), "mp4");
    await waitFor(() => expect(screen.getByRole("radiogroup", { name: "Look" })).toBeInTheDocument());
    await user.click(tile("Title page", "Title page"));
    await user.click(choice("Title page style", "Rise"));
    await user.click(tile("Transition", "Wipe"));
    await user.click(tile("Look", "Clean"));
    expect(screen.queryByRole("group", { name: "Title page style" })).toBeNull();
    expect(within(screen.getByRole("radiogroup", { name: "Transition" })).queryByRole("radio", { name: "Wipe" })).toBeNull();

    await waitFor(() => {
      const bodies = vi.mocked(api.exportPreview).mock.calls.map((c) => c[1]);
      expect(bodies.some((b) => b.look === "clean" && b.card === "title")).toBe(true);
    });
    await user.click(screen.getByRole("button", { name: /export bundle/i }));
    await waitFor(() => expect(api.exportMatch).toHaveBeenCalledTimes(1));
    const body = vi.mocked(api.exportMatch).mock.calls[0][1];
    expect(body.overlay_theme).toBe("clean");
    expect("title_page_variant" in body).toBe(false);
    expect(body.transition_kind).toBe("none");
  });
});
