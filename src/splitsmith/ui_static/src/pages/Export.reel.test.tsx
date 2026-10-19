/**
 * Export's fourth mode, the highlight reel (spec 2026-10-18-highlight-reel-
 * design, part 2): the moment menu with its defaults ticked, the cut and
 * the format, the one Render button with its reason when it cannot, and
 * `?mode=reel` (the Overview card's link) opening straight into it.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ExportOverview, type HighlightReadiness, type Job, type MatchProject } from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS, settingsToBody } from "@/lib/exportPresets";
import { ConfirmProvider } from "@/components/useConfirm";
import { Export } from "@/pages/Export";

const deployment = vi.hoisted(() => ({ mode: "local" as "hosted" | "local" }));

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: vi.fn(() => ({ mode: deployment.mode, resolved: true })) };
});

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
      getHighlights: vi.fn(),
      getExportPresets: vi.fn().mockResolvedValue({ presets: [] }),
      getYouTubeSettings: vi.fn().mockRejectedValue(new Error("not connected")),
      submitHighlightReel: vi.fn(),
      pollJob: vi.fn(),
      revealFile: vi.fn(),
    },
  };
});

const PROJECT = {
  schema_version: 1,
  name: "bromma-2026",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  competitor_name: "Mathias",
  stages: [],
  unassigned_videos: [],
  trim_pre_buffer_seconds: 5,
  trim_post_buffer_seconds: 5,
  automation: {},
  nudges_dismissed_stages: [],
  raw_videos: [],
  origin: "local",
} as unknown as MatchProject;
const OVERVIEW: ExportOverview = { match_exports: [], stages: [] };

function readiness(over: Partial<HighlightReadiness> = {}): HighlightReadiness {
  return {
    unlocked: true,
    ready: 2,
    total: 2,
    stages: [
      { stage_number: 1, state: "ready", proposals: 0 },
      { stage_number: 2, state: "ready", proposals: 0 },
    ],
    menu: [
      { kind: "fastest_draw", label: "FASTEST DRAW", group: "default", default: true },
      { kind: "slowest_draw", label: "COFFEE BREAK", group: "lolz", default: false },
      { kind: "fastest_split", label: "FASTEST SPLIT", group: "default", default: true },
    ],
    cuts: ["normal", "short"],
    ...over,
  };
}

function job(over: Partial<Job> = {}): Job {
  return {
    id: "job-9",
    kind: "highlight_reel",
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
    result: { filename: "bromma-2026-highlights.mp4", duration_seconds: 31.5, degradations: [], left_out: [] },
    timings: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function Shell({ origin = "local", capabilities = ["edit", "review"] }: { origin?: string; capabilities?: string[] }) {
  return (
    <Outlet
      context={{
        project: PROJECT,
        health: { project_root: "/m/bromma" },
        shooters: [],
        refresh: () => {},
        origin,
        capabilities,
      }}
    />
  );
}

function renderAt(path: string, shell: { origin?: string; capabilities?: string[] } = {}) {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={[path]}>
      <ConfirmProvider>
        <Routes>
          <Route path="/match/:matchId" element={<Shell {...shell} />}>
            <Route path="export/:slug" element={<Export />} />
          </Route>
        </Routes>
      </ConfirmProvider>
    </MemoryRouter>,
  );
  return user;
}

beforeEach(() => {
  // No test leaks into the next: the remembered settings and the presets
  // a test set are reset (``clearAllMocks`` keeps implementations).
  window.localStorage.clear();
  deployment.mode = "local";
  vi.mocked(api.getExportPresets).mockResolvedValue({ presets: [] });
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
  vi.mocked(api.getExportOverview).mockResolvedValue(OVERVIEW);
  vi.mocked(api.getHighlights).mockResolvedValue(readiness());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Export highlight reel mode", () => {
  it("is a mode of its own with the default moments ticked and no stage table", async () => {
    const user = renderAt("/match/m1/export/mathias");
    await user.click(await screen.findByRole("button", { name: /highlight reel/i }));
    expect(await screen.findByRole("button", { name: "Fastest draw" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Coffee break" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByText(/exportable/)).toBeNull();
  });

  it("opens straight into the mode from the Overview card's link", async () => {
    renderAt("/match/m1/export/mathias?mode=reel");
    expect(await screen.findByRole("button", { name: "Render reel" })).toBeInTheDocument();
  });

  it("stays in the mode when the page's starting preset arrives later", async () => {
    vi.mocked(api.getExportPresets).mockResolvedValue({
      presets: [
        {
          preset_id: "youtube",
          name: "YouTube match video",
          builtin: true,
          // A whole body, as the server always answers (every field defaults).
          body: { ...settingsToBody(DEFAULT_EXPORT_SETTINGS), mode: "single" },
        },
      ],
    } as never);
    renderAt("/match/m1/export/mathias?mode=reel");
    expect(await screen.findByRole("button", { name: "Render reel" })).toBeInTheDocument();
    await waitFor(() => expect(api.getExportPresets).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByRole("button", { name: "Render reel" })).toBeInTheDocument();
  });

  it("will not render with no moment chosen, and says so", async () => {
    const user = renderAt("/match/m1/export/mathias?mode=reel");
    await user.click(await screen.findByRole("button", { name: "Fastest draw" }));
    await user.click(screen.getByRole("button", { name: "Fastest split" }));
    expect(screen.getByRole("button", { name: "Render reel" })).toBeDisabled();
    expect(screen.getByText("Choose at least one moment.")).toBeInTheDocument();
  });

  it("renders the chosen moments in the chosen cut and format, then re-reads the history", async () => {
    vi.mocked(api.submitHighlightReel).mockResolvedValue(job({ status: "pending", result: null }));
    vi.mocked(api.pollJob).mockResolvedValue(job());
    const user = renderAt("/match/m1/export/mathias?mode=reel");
    await user.click(await screen.findByRole("button", { name: "Coffee break" }));
    await user.click(screen.getByRole("button", { name: "Shorts" }));
    await user.click(screen.getByRole("button", { name: "Landscape" }));
    const runsBefore = vi.mocked(api.getExportRuns).mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Render reel" }));
    expect(api.submitHighlightReel).toHaveBeenCalledWith("mathias", {
      moments: ["fastest_draw", "slowest_draw", "fastest_split"],
      short: true,
      upright: false,
      force: false,
      youtube_upload: false,
      youtube_privacy: "unlisted",
      main_camera: "default",
    });
    expect(await screen.findByRole("status")).toHaveTextContent(/Wrote bromma-2026-highlights\.mp4 \(31\.5 s\)/);
    await waitFor(() => expect(vi.mocked(api.getExportRuns).mock.calls.length).toBeGreaterThan(runsBefore));
  });

  it("sends one render however fast Render is clicked", async () => {
    vi.mocked(api.submitHighlightReel).mockReturnValue(new Promise(() => {}));
    const user = renderAt("/match/m1/export/mathias?mode=reel");
    const render = await screen.findByRole("button", { name: "Render reel" });
    await user.click(render);
    await user.click(render);
    expect(api.submitHighlightReel).toHaveBeenCalledTimes(1);
  });

  it("offers to leave stages out after a render that could not use one", async () => {
    vi.mocked(api.submitHighlightReel).mockResolvedValue(job({ status: "pending", result: null }));
    vi.mocked(api.pollJob).mockResolvedValue(
      job({ status: "failed", result: null, error: "the reel could not use stage 2 (its trim is missing)." }),
    );
    const user = renderAt("/match/m1/export/mathias?mode=reel");
    expect(screen.queryByRole("button", { name: "Render the ready ones" })).toBeNull();
    await user.click(await screen.findByRole("button", { name: "Render reel" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/stage 2/);
    await user.click(screen.getByRole("button", { name: "Render the ready ones" }));
    await user.click(screen.getByRole("button", { name: "Render reel" }));
    expect(vi.mocked(api.submitHighlightReel).mock.calls.at(-1)?.[1]).toMatchObject({ force: true });
  });

  it("is not offered on a desktop mirror, which cannot render it", async () => {
    deployment.mode = "hosted";
    renderAt("/match/m1/export/mathias", { origin: "desktop", capabilities: ["review"] });
    await screen.findByRole("group", { name: "Output mode" });
    expect(screen.queryByRole("button", { name: /highlight reel/i })).toBeNull();
  });

  it("offers no YouTube upload while YouTube is not connected", async () => {
    renderAt("/match/m1/export/mathias?mode=reel");
    await screen.findByRole("button", { name: "Render reel" });
    expect(screen.queryByRole("group", { name: "After render" })).toBeNull();
  });

  it("uploads to YouTube after the render when asked", async () => {
    vi.mocked(api.getYouTubeSettings).mockResolvedValueOnce({ connected: true } as never);
    vi.mocked(api.submitHighlightReel).mockResolvedValue(job({ status: "pending", result: null }));
    vi.mocked(api.pollJob).mockResolvedValue(job());
    const user = renderAt("/match/m1/export/mathias?mode=reel");
    await user.click(await screen.findByRole("button", { name: "Upload unlisted" }));
    await user.click(screen.getByRole("button", { name: "Render reel" }));
    expect(vi.mocked(api.submitHighlightReel).mock.calls.at(-1)?.[1]).toMatchObject({
      youtube_upload: true,
      youtube_privacy: "unlisted",
    });
  });

  it("offers to render the ready stages while others are unfinished", async () => {
    vi.mocked(api.getHighlights).mockResolvedValue(
      readiness({
        unlocked: false,
        ready: 1,
        stages: [
          { stage_number: 1, state: "ready", proposals: 0 },
          { stage_number: 2, state: "proposals_left", proposals: 2 },
        ],
      }),
    );
    const user = renderAt("/match/m1/export/mathias?mode=reel");
    expect(await screen.findByRole("link", { name: "Stage 2: 2 proposals left in Breakdown" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Render reel" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Render the ready ones" }));
    expect(screen.getByRole("button", { name: "Render reel" })).toBeEnabled();
  });
});
