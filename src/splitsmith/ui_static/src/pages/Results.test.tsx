import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { api, type MatchProject, type ShooterListEntry, type StageStatus } from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";

import { Results } from "@/pages/Results";

// Hosted-only chrome (Share button) is out of scope here; pin local mode.
// Individual cases re-mock useDeploymentMode for the hosted-link gating.
vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: vi.fn(() => ({ mode: "local" as const, resolved: true })),
  };
});

// Multi-shooter Results fetches every shooter's project for stage times.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn().mockImplementation(() => new Promise(() => {})),
      // Local-mode hosted link (see "Share on splitsmith.app" below).
      // Default: never synced, so the existing row cases see no link.
      getSyncStatus: vi.fn().mockResolvedValue({
        configured: false,
        last_synced_at: null,
        stale: true,
        pending_media: 0,
        errors: [],
        remote_changes: null,
      }),
      getSyncSettings: vi.fn().mockResolvedValue({
        base_url: "https://splitsmith.app",
        token_set: false,
        account: null,
      }),
    },
  };
});

function makeShooter(
  slug: string,
  name: string,
  statuses: [number, StageStatus][],
): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: statuses.filter(([, s]) => s === "audited").length,
    stages_total: statuses.length,
    video_count: 0,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: statuses.map(([stage_number, status]) => ({ stage_number, status })),
  };
}

function makeProject(): MatchProject {
  return {
    schema_version: 1,
    name: "bromma-2026",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    competitor_name: null,
    scoreboard_match_id: "sb-1",
    scoreboard_content_type: 1,
    selected_shooter_id: null,
    selected_competitor_id: null,
    shooter_token: null,
    match_date: "2026-06-27",
    stages: [
      {
        stage_number: 1,
        stage_name: "Steel Rush",
        time_seconds: 20,
        scorecard_updated_at: "2026-06-28T09:40:00Z",
        videos: [],
        skipped: false,
        placeholder: false,
        time_seconds_manual: false,
        status: "audited",
        stage_rounds: null,
        scorecard: {
          hit_factor: 5.15,
          stage_points: 103,
          stage_pct: 71.5,
          alphas: 10,
          charlies: 16,
          deltas: 5,
          misses: 0,
          no_shoots: 0,
          procedurals: 0,
          dq: false,
        },
        figures: { draw: 1.84, avg_split: 0.52, fastest_split: 0.31, shot_count: 28, split_count: 9 },
      },
      {
        stage_number: 2,
        stage_name: "Brass Monkey",
        time_seconds: 0,
        scorecard_updated_at: null,
        videos: [{ role: "primary" } as unknown as MatchProject["stages"][number]["videos"][number]],
        skipped: false,
        placeholder: false,
        time_seconds_manual: false,
        status: "ready",
        stage_rounds: null,
        scorecard: null,
        figures: null,
      },
      {
        stage_number: 3,
        stage_name: "Quiet",
        time_seconds: 0,
        scorecard_updated_at: null,
        videos: [],
        skipped: false,
        placeholder: false,
        time_seconds_manual: false,
        status: "todo",
        stage_rounds: null,
        scorecard: null,
        figures: null,
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
  };
}

const SOLO = [makeShooter("anna", "Anna", [[1, "audited"], [2, "ready"], [3, "todo"]])];
const TRIO = [
  makeShooter("anna", "Anna", [[1, "audited"], [2, "ready"], [3, "todo"]]),
  makeShooter("bjorn", "Bjorn", [[1, "ready"], [2, "todo"], [3, "todo"]]),
  makeShooter("cleo", "Cleo", [[1, "skipped"], [2, "todo"], [3, "todo"]]),
];

function Shell({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderResults(path: string, shooters: ShooterListEntry[] = SOLO) {
  const ctx: MatchShellOutletContext = {
    project: makeProject(),
    health: null,
    shooters,
    refresh: vi.fn(),
    origin: null,
    capabilities: null,
  };
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Shell ctx={ctx} />}>
          <Route path="/match/:matchId/results" element={<Results />} />
          <Route path="/share/:token/results" element={<Results />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function rowOf(text: string): HTMLElement {
  const tr = screen.getByText(text).closest("tr");
  if (!tr) throw new Error(`no row for ${text}`);
  return tr;
}

describe("Splits - owner surface", () => {
  it("renders the header, five stats and one row per stage with splits before scoring", () => {
    renderResults("/match/m1/results");
    expect(screen.getByRole("heading", { level: 1, name: "Splits" })).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 stages audited/)).toBeInTheDocument();
    expect(screen.getByText(/Scorecard synced/)).toBeInTheDocument();
    expect(screen.getByText("Avg draw")).toBeInTheDocument();
    expect(screen.getByText("Fastest split")).toBeInTheDocument();
    const tr = rowOf("Steel Rush");
    const cells = within(tr).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.slice(0, 9)).toEqual(["01", "Steel Rush", "1.84", "0.520", "0.310", "28", "20.00", "5.15", "10A 16C 5D"]);
    expect(within(tr).getByRole("link", { name: "Play stage 1" })).toHaveAttribute("href", "/match/m1/results/anna/1");
  });

  it("keeps not-audited rows with an Audit link and collapses no-footage stages", () => {
    renderResults("/match/m1/results");
    expect(within(rowOf("Brass Monkey")).getByRole("link", { name: "Audit" })).toHaveAttribute("href", "/match/m1/audit/anna/2");
    expect(within(rowOf("Quiet")).getByText("no footage")).toBeInTheDocument();
  });

  it("the one primary is Play all, pointing at the first audited stage with ?play=all", () => {
    renderResults("/match/m1/results");
    expect(screen.getByRole("link", { name: "Play all" })).toHaveAttribute("href", "/match/m1/results/anna/1?play=all");
  });

  it("offers the refresh glyph on a scoreboard-linked match", () => {
    renderResults("/match/m1/results");
    expect(screen.getByRole("button", { name: "Refresh from scoreboard" })).toBeInTheDocument();
  });

  it("multi-shooter: chips filter the table to one shooter", async () => {
    vi.mocked(api.getProject).mockImplementation((slug: string) =>
      Promise.resolve({
        ...makeProject(),
        stages: makeProject().stages.map((st) =>
          st.stage_number === 1
            ? {
                ...st,
                status: "ready" as const,
                figures: null,
                videos: [{ role: "primary" } as unknown as MatchProject["stages"][number]["videos"][number]],
                scorecard: { ...st.scorecard!, hit_factor: 2.9 },
              }
            : st,
        ),
        competitor_name: slug,
      }),
    );
    renderResults("/match/m1/results", TRIO);
    expect(screen.getByRole("group", { name: "Filter by shooter" })).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 stage takes audited/)).toBeInTheDocument();
    await vi.waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Bjorn" }));
    await screen.findByText("2.90");
    expect(within(rowOf("Steel Rush")).getByText(/not audited/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Play all" })).toBeNull();
    vi.mocked(api.getProject).mockImplementation(() => new Promise(() => {}));
  });
});

describe("Splits - share surface", () => {
  it("reads no video, shows the date line, and offers no Share, refresh or Audit link", () => {
    renderResults("/share/tok/results");
    expect(screen.getByText(/27 Jun 2026/)).toBeInTheDocument();
    expect(screen.getByText(/1 stage on video/)).toBeInTheDocument();
    expect(screen.queryByText(/audited/)).toBeNull();
    expect(screen.queryByRole("link", { name: "Audit" })).toBeNull();
    expect(screen.queryByRole("button", { name: /refresh/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /share/i })).toBeNull();
    expect(screen.getAllByText("no video").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Play stage 1" })).toHaveAttribute("href", "/share/tok/results/anna/1");
    expect(screen.getByRole("link", { name: "Play all" })).toHaveAttribute("href", "/share/tok/results/anna/1?play=all");
  });
});

describe("Results - Share on splitsmith.app (local mode)", () => {
  const synced = {
    configured: true,
    last_synced_at: "2026-09-01T00:00:00Z",
    stale: false,
    pending_media: 0,
    errors: [],
    remote_changes: null,
  };

  beforeEach(() => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
    vi.mocked(api.getSyncStatus).mockClear();
  });

  it("links a synced match to its hosted results page", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(synced);
    renderResults("/match/m1/results");

    const link = await screen.findByRole("link", { name: /share on splitsmith\.app/i });
    expect(link).toHaveAttribute("href", "https://splitsmith.app/match/m1/results");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("warns when local changes have not been pushed yet", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue({ ...synced, stale: true, pending_media: 2 });
    renderResults("/match/m1/results");

    await screen.findByRole("link", { name: /share on splitsmith\.app/i });
    expect(screen.getByText(/unsynced changes/i)).toBeInTheDocument();
  });

  it("omits the link before the first push", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue({ ...synced, last_synced_at: null, stale: true });
    renderResults("/match/m1/results");

    await vi.waitFor(() => expect(api.getSyncStatus).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: /share on splitsmith\.app/i })).toBeNull();
  });

  it("never probes the local-only sync endpoints in hosted mode", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    renderResults("/match/m1/results");

    await screen.findByRole("button", { name: /manage share links/i });
    expect(api.getSyncStatus).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: /share on splitsmith\.app/i })).toBeNull();
  });

  it("never probes them on the anonymous share surface", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(synced);
    renderResults("/share/tok/results");

    await new Promise((r) => setTimeout(r, 0));
    expect(api.getSyncStatus).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: /share on splitsmith\.app/i })).toBeNull();
  });
});
