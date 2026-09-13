import { render, screen } from "@testing-library/react";
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
    scoreboard_match_id: null,
    scoreboard_content_type: null,
    selected_shooter_id: null,
    selected_competitor_id: null,
    shooter_token: null,
    match_date: null,
    stages: [
      {
        stage_number: 1,
        stage_name: "Steel Rush",
        time_seconds: 20,
        scorecard_updated_at: null,
        videos: [],
        skipped: false,
        placeholder: false,
        time_seconds_manual: false,
        stage_rounds: null,
        scorecard: null,
      },
      {
        stage_number: 2,
        stage_name: "Brass Monkey",
        time_seconds: 0,
        scorecard_updated_at: null,
        videos: [],
        skipped: false,
        placeholder: false,
        time_seconds_manual: false,
        stage_rounds: null,
        scorecard: null,
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

const SHOOTERS = [
  makeShooter("anna", "Anna", [[1, "audited"], [2, "ready"]]),
  makeShooter("bjorn", "Bjorn", [[1, "ready"], [2, "todo"]]),
  makeShooter("cleo", "Cleo", [[1, "skipped"], [2, "todo"]]),
];

function Shell({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderResults(path: string) {
  const ctx: MatchShellOutletContext = {
    project: makeProject(),
    health: null,
    shooters: SHOOTERS,
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

// At jsdom's default viewport both the mobile cards and the desktop
// matrix render (Tailwind lg: classes are media-query CSS jsdom does
// not apply), hence getAllByText for row-level assertions.

describe("Results rows - owner surface", () => {
  it("gives audited rows a watch affordance instead of the audited chip", () => {
    renderResults("/match/m1/results");
    expect(screen.getAllByText(", watch run").length).toBeGreaterThan(0);
    expect(screen.queryByText("Audited")).not.toBeInTheDocument();
  });

  it("keeps operator status chips on non-audited rows", () => {
    renderResults("/match/m1/results");
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Skipped").length).toBeGreaterThan(0);
  });

  it("keeps the audited wording in the header counter", () => {
    renderResults("/match/m1/results");
    // /audited/ also matches the "Not audited" row labels; the header
    // check is that the videos wording never leaks onto the owner view.
    expect(screen.getAllByText(/audited/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/videos/)).not.toBeInTheDocument();
  });
});

describe("Results rows - share surface", () => {
  it("gives audited rows the watch affordance", () => {
    renderResults("/share/tok123/results");
    expect(screen.getAllByText(", watch run").length).toBeGreaterThan(0);
  });

  it("collapses non-audited and skipped rows to a No video label", () => {
    renderResults("/share/tok123/results");
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
    expect(screen.queryByText("Skipped")).not.toBeInTheDocument();
    expect(screen.queryByText("Not audited")).not.toBeInTheDocument();
    expect(screen.getAllByText("No video").length).toBeGreaterThan(0);
  });

  it("counts videos, not audits, in the header", () => {
    renderResults("/share/tok123/results");
    expect(screen.getAllByText(/videos/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/audited/)).not.toBeInTheDocument();
  });

  it("collapses a stage with no watchable runs to one No videos yet line", () => {
    renderResults("/share/tok123/results");
    // Stage 2 has no audited cell: the per-shooter No video rows give way
    // to a single line (mobile card + desktop matrix render one each).
    expect(screen.getAllByText("No videos yet").length).toBeGreaterThan(0);
    // Stage 1 has a watchable run, so its non-audited siblings keep their
    // per-shooter No video rows.
    expect(screen.getAllByText("No video").length).toBeGreaterThan(0);
  });

  it("never collapses stages on the owner surface", () => {
    renderResults("/match/m1/results");
    expect(screen.queryByText("No videos yet")).not.toBeInTheDocument();
  });
});

describe("Results rows - hit counts", () => {
  it("renders the A/C/D/NS/M/P breakdown on scored audited rows", async () => {
    const scored = makeProject();
    scored.stages[0].scorecard = {
      hit_factor: 5.24,
      stage_points: 100,
      stage_pct: 87.5,
      alphas: 10,
      charlies: 2,
      deltas: 1,
      misses: 0,
      no_shoots: 0,
      procedurals: 3,
      dq: false,
    };
    const { api } = await import("@/lib/api");
    vi.mocked(api.getProject).mockResolvedValue(scored);
    try {
      renderResults("/match/m1/results");
      // Multi-shooter cells resolve per-shooter projects async.
      expect((await screen.findAllByText("NS")).length).toBeGreaterThan(0);
      expect(screen.getAllByText("P").length).toBeGreaterThan(0);
      expect(screen.getAllByText("3").length).toBeGreaterThan(0);
    } finally {
      vi.mocked(api.getProject).mockImplementation(() => new Promise(() => {}));
    }
  });

  it("keeps unscored audited rows free of hit-count chrome", () => {
    renderResults("/match/m1/results");
    expect(screen.queryByText("NS")).not.toBeInTheDocument();
  });
});

// Local mode has no Share dialog - share links are minted on hosted. A
// match that has been pushed at least once gets a deep link to its
// hosted results page instead, where the real Share button lives.
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
