/**
 * The preset row on Export (spec 2026-09-15 s1): the built-ins open the
 * page, applying one changes the form and the request body, an edit
 * turns the row to Custom, Save as... stores the body, and a reload
 * lands where the form was left.
 */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import {
  api,
  type ExportOverview,
  type ExportPreset,
  type ExportPresetBody,
  type Job,
  type MatchProject,
  type ShooterListEntry,
  type StageExportStatus,
} from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS, settingsToBody } from "@/lib/exportPresets";
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
      getExportPresets: vi.fn(),
      putExportPreset: vi.fn(),
      deleteExportPreset: vi.fn(),
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

const DEFAULT_BODY = settingsToBody(DEFAULT_EXPORT_SETTINGS);

function preset(id: string, name: string, body: Partial<ExportPresetBody> = {}): ExportPreset {
  return { preset_id: id, name, builtin: true, updated_at: "2026-09-15T00:00:00Z", body: { ...DEFAULT_BODY, ...body } };
}

/** What the API's built-ins carry, as far as these tests read them. */
const BUILTINS: ExportPreset[] = [
  preset("builtin:final-cut", "Final Cut bundle"),
  preset("builtin:youtube", "YouTube match video", {
    output_format: "mp4",
    youtube_preset: true,
    padding_preset: "action",
    head_pad_seconds: 0.5,
    tail_pad_seconds: 1,
    title_page: true,
    closing_card: true,
    stage_card_style: "slate",
    summary_hold_seconds: 3,
    overlay: true,
  }),
  preset("builtin:trims", "Quick trims", { mode: "trims", padding_preset: "action", head_pad_seconds: 0.5, tail_pad_seconds: 1 }),
  preset("builtin:compare", "Compare grid", { mode: "compare", canvas: "hd", grid_overlay: true, grid_hold_seconds: 3 }),
];

beforeEach(() => {
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
  vi.mocked(api.getExportOverview).mockResolvedValue(OVERVIEW);
  // A tiny server: what PUT stores, the next GET lists after the built-ins.
  const saved: ExportPreset[] = [];
  vi.mocked(api.getExportPresets).mockImplementation(async () => ({ presets: [...BUILTINS, ...saved] }));
  vi.mocked(api.putExportPreset).mockImplementation(async (id, name, body) => {
    const row = { preset_id: id === "new" ? "p-new" : id, name, builtin: false, updated_at: "2026-09-15T00:00:00Z", body };
    saved.push(row);
    return row;
  });
  vi.mocked(api.deleteExportPreset).mockResolvedValue(undefined);
  window.localStorage.clear();
});
afterEach(() => vi.clearAllMocks());

/** The Cut group has its own "Custom" padding option; look only in the preset row. */
function noCustomPreset(): boolean {
  return within(screen.getByRole("group", { name: "Preset" })).queryByRole("button", { name: "Custom" }) === null;
}

function toggle(name: string): HTMLElement {
  return screen.getByRole("button", { name: new RegExp(`^${name}$`) });
}

describe("Export presets", () => {
  it("opens on the built-ins with Final Cut bundle active and the groups closed", async () => {
    await renderPage();
    await waitFor(() => expect(choice("Preset", "Final Cut bundle")).toHaveAttribute("aria-pressed", "true"));
    expect(toggle("Output")).toHaveAttribute("aria-expanded", "false");
    expect(toggle("Cut")).toHaveAttribute("aria-expanded", "false");
    expect(toggle("Look")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Full 5.0 / 5.0 s")).toBeInTheDocument();
    expect(noCustomPreset()).toBe(true);
  });

  it("applying the YouTube preset changes the form and the request body", async () => {
    const { user } = await renderPage();
    vi.mocked(api.exportMatch).mockResolvedValue(job({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(job({ status: "succeeded" }));
    await user.click(choice("Preset", "YouTube match video"));
    expect(screen.getByText(/MP4 · YouTube preset/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /export bundle/i }));
    await waitFor(() => expect(api.exportMatch).toHaveBeenCalled());
    const body = vi.mocked(api.exportMatch).mock.calls[0][1];
    expect(body.output_format).toBe("mp4");
    expect(body.youtube_preset).toBe(true);
    expect(body.head_pad_seconds).toBe(0.5);
    expect(body.title_page).toBe(true);
    expect(body.include_overlay).toBe(true);
  });

  it("applying a preset with another mode switches the mode", async () => {
    const { user } = await renderPage();
    await user.click(choice("Preset", "Quick trims"));
    expect(choice("Output mode", "Trims only")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /export trims/i })).toBeInTheDocument();
    expect(noCustomPreset()).toBe(true);
  });

  it("a compare preset is offered greyed on a one-shooter match and applying it changes nothing", async () => {
    const { user } = await renderPage();
    const compare = choice("Preset", "Compare grid");
    expect(compare).toBeDisabled();
    expect(compare).toHaveAttribute("title", expect.stringMatching(/two or more shooters/i));
    await user.click(compare);
    expect(choice("Preset", "Final Cut bundle")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Full 5.0 / 5.0 s")).toBeInTheDocument();
    expect(noCustomPreset()).toBe(true);
  });

  it("on a two-shooter match the compare preset applies and switches the mode", async () => {
    const { user } = await renderPage([shooter("mathias", "Mathias"), shooter("casper", "Casper")]);
    await user.click(choice("Preset", "Compare grid"));
    expect(choice("Output mode", "Compare grid")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /render grid/i })).toBeInTheDocument();
    expect(noCustomPreset()).toBe(true);
  });

  it("after Save as... the row shows the server's list, not a client-side sort", async () => {
    vi.mocked(api.getExportPresets)
      .mockResolvedValueOnce({ presets: BUILTINS })
      .mockResolvedValueOnce({ presets: [...BUILTINS, { ...preset("p-b", "b night"), builtin: false }, { ...preset("p-new", "Club night"), builtin: false }] });
    const { user } = await renderPage();
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Save as..." }));
    await user.type(within(screen.getByRole("dialog")).getByLabelText("Name"), "Club night");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(choice("Preset", "Club night")).toHaveAttribute("aria-pressed", "true"));
    const names = within(screen.getByRole("group", { name: "Preset" }))
      .getAllByRole("button")
      .map((b) => b.textContent);
    expect(names.slice(-2)).toEqual(["b night", "Club night"]);
  });

  it("editing a field shows Custom, from the preset it started on", async () => {
    const { user } = await renderPage();
    await user.click(toggle("Cut"));
    await user.click(choice("Trim padding", "Action"));
    expect(choice("Preset", "Custom")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("from Final Cut bundle")).toBeInTheDocument();
  });

  it("editing a match-specific field does not dirty the preset", async () => {
    const { user } = await renderPage();
    await user.clear(screen.getByLabelText("Bundle name"));
    await user.type(screen.getByLabelText("Bundle name"), "cut-1");
    expect(noCustomPreset()).toBe(true);
  });

  it("Save as... stores the current body under a new name and selects it", async () => {
    const { user } = await renderPage();
    await user.click(toggle("Cut"));
    await user.click(choice("Trim padding", "Highlight"));
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Save as..." }));
    const dialog = screen.getByRole("dialog", { name: "Save preset" });
    await user.type(within(dialog).getByLabelText("Name"), "Club night");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.putExportPreset).toHaveBeenCalledWith(
        "new",
        "Club night",
        expect.objectContaining({ padding_preset: "highlight", head_pad_seconds: 1.5 }),
      ),
    );
    await waitFor(() => expect(choice("Preset", "Club night")).toHaveAttribute("aria-pressed", "true"));
    expect(noCustomPreset()).toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("restores last-used on reload", async () => {
    const { user } = await renderPage();
    await user.click(choice("Preset", "YouTube match video"));
    await user.click(toggle("Cut"));
    await user.click(choice("Trim padding", "Highlight"));
    await waitFor(() =>
      expect(JSON.parse(window.localStorage.getItem("splitsmith.export.lastUsed")!).body.padding_preset).toBe(
        "highlight",
      ),
    );
    cleanup();
    await renderPage();
    await waitFor(() => expect(choice("Preset", "Custom")).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByText("from YouTube match video")).toBeInTheDocument();
    expect(screen.getByText(/Highlight 1\.5 \/ 2\.0 s/)).toBeInTheDocument();
  });

  it("a failed preset load still renders the page with Custom only and no error", async () => {
    vi.mocked(api.getExportPresets).mockRejectedValue(new Error("offline"));
    await renderPage();
    const row = within(screen.getByRole("group", { name: "Preset" })).getAllByRole("button");
    expect(row.map((b) => b.textContent)).toEqual(["Custom"]);
    expect(choice("Preset", "Custom")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
