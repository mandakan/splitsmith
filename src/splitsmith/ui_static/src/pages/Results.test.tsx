import { render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import type { MatchProject, ShooterListEntry, StageStatus } from "@/lib/api";

import { Results } from "@/pages/Results";

// Hosted-only chrome (Share button) is out of scope here; pin local mode.
vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => "local" as const };
});

// Multi-shooter Results fetches every shooter's project for stage times.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn().mockImplementation(() => new Promise(() => {})),
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
  };
}

const SHOOTERS = [
  makeShooter("anna", "Anna", [[1, "audited"]]),
  makeShooter("bjorn", "Bjorn", [[1, "ready"]]),
  makeShooter("cleo", "Cleo", [[1, "skipped"]]),
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
    expect(screen.getByText(/videos/)).toBeInTheDocument();
    expect(screen.queryByText(/audited/)).not.toBeInTheDocument();
  });
});
