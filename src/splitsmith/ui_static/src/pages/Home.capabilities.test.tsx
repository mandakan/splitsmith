/**
 * Home capability gating (#756).
 *
 * Home used to hide its edit affordances ("Edit Stages", "Add a
 * squadmate") whenever the outlet context's ``origin`` was "desktop" - a
 * proxy for "this project is a read-only mirror". The #631 transfer
 * endgame breaks that proxy: a mirror that completes its transfer keeps
 * ``origin === "desktop"`` forever (origin is provenance, never
 * recomputed) but gains the ``edit`` capability. These tests pin the
 * replacement gate - ``capabilityDenied(capabilities, "edit")`` - and
 * prove it is independent of origin.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { ConfirmProvider } from "@/components/useConfirm";
import { api, type MatchProject, type ShooterListEntry } from "@/lib/api";
import { Home } from "@/pages/Home";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn(),
    },
  };
});

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    // "hosted" so SyncCard (local-only) never mounts - it has its own
    // API surface unrelated to this file's gate.
    useDeploymentMode: vi.fn(() => ({ mode: "hosted" as const, resolved: true })),
  };
});

function projectFixture(): MatchProject {
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
        stage_name: "Stage One",
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
    origin: "desktop",
  };
}

function shooterFixture(): ShooterListEntry {
  return {
    slug: "mathias",
    name: "Mathias",
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: 0,
    stages_total: 1,
    video_count: 0,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: [],
  };
}

function OutletCtx({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderHome(ctx: Partial<MatchShellOutletContext>) {
  const base: MatchShellOutletContext = {
    project: projectFixture(),
    health: null,
    shooters: [shooterFixture()],
    refresh: () => {},
    origin: "desktop",
    capabilities: ["review", "share_manage"],
    ...ctx,
  };
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1"]}>
        <Routes>
          <Route path="/match/:matchId" element={<OutletCtx ctx={base} />}>
            <Route index element={<Home />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

describe("Home capability gating", () => {
  beforeEach(() => {
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/root",
      match_name: "bromma-2026",
      shooters: [shooterFixture()],
      origin: "desktop",
      capabilities: ["review", "share_manage"],
    });
  });

  it("hides edit entry points when the capability set lacks edit", async () => {
    renderHome({ capabilities: ["review", "share_manage"] });
    await waitFor(() => expect(api.listMatchShooters).toHaveBeenCalled());
    // "Edit Stages" (exact, title case): the page-header button. A
    // case-insensitive regex would also catch the EmptyVariant help
    // card's "Edit stages" cta, which this test doesn't target.
    expect(screen.queryByRole("button", { name: "Edit Stages" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: /add a squadmate/i }),
    ).toBeNull();
  });

  it("shows edit entry points on a desktop-origin match WITH edit (forward compat)", async () => {
    // The #631 transfer endgame: origin stays "desktop" forever, but a
    // completed transfer grants edit - the page must not test origin.
    renderHome({
      origin: "desktop",
      capabilities: ["edit", "review", "share_manage"],
    });
    await waitFor(() => expect(api.listMatchShooters).toHaveBeenCalled());
    expect(
      await screen.findByRole("button", { name: "Edit Stages" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /add a squadmate/i }),
    ).toBeInTheDocument();
  });
});
