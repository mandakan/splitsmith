/**
 * Shooters page capability gating (#756 review fix 2).
 *
 * Shooters is mounted under <MatchShell/> and reads its capability set
 * from the outlet context (the same ``capabilities`` field Home reads,
 * not ``project.capabilities`` - see MatchShellOutletContext). Its
 * primary purpose is the add-shooter form, so that's DISABLED (with
 * READ_ONLY_MIRROR_MESSAGE as the visible reason) rather than hidden --
 * an Ingest-style page with no controls at all would read as broken.
 * Remove and rebuild-trims are secondary, edit-class writes; this page's
 * own idiom already disables (not hides) unavailable row actions (see
 * the Audit button's ``video_count === 0`` gate), so the same idiom
 * applies here rather than introducing a new hide pattern.
 *
 * These tests mirror Home.capabilities.test.tsx's outlet-context
 * scaffold.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { ConfirmProvider } from "@/components/useConfirm";
import {
  api,
  READ_ONLY_MIRROR_MESSAGE,
  type MatchProject,
  type ShooterListEntry,
} from "@/lib/api";
import { Shooters } from "@/pages/Shooters";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn(),
      addMatchShooter: vi.fn(),
      removeMatchShooter: vi.fn(),
      buildShooterTrimCaches: vi.fn(),
    },
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
    video_count: 1,
    cameras: [],
    // >0 so the "Rebuild" control renders for the disabled/enabled check.
    stages_missing_trim: 2,
    stage_statuses: [],
  };
}

function OutletCtx({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderShooters(ctx: Partial<MatchShellOutletContext>) {
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
      <MemoryRouter initialEntries={["/match/m1/shooters"]}>
        <Routes>
          <Route path="/match/:matchId" element={<OutletCtx ctx={base} />}>
            <Route path="shooters" element={<Shooters />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

describe("Shooters capability gating", () => {
  beforeEach(() => {
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/root",
      match_name: "bromma-2026",
      shooters: [shooterFixture()],
      origin: "desktop",
      capabilities: ["review", "share_manage"],
    });
  });

  it("disables the add-shooter form and the per-shooter edit controls when edit is denied", async () => {
    renderShooters({ capabilities: ["review", "share_manage"] });
    await waitFor(() => expect(api.listMatchShooters).toHaveBeenCalled());
    await screen.findByText("Mathias");

    expect(
      screen.getByText(READ_ONLY_MIRROR_MESSAGE),
    ).toBeInTheDocument();

    expect(screen.getByPlaceholderText("Johan Larsson")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /add shooter/i }),
    ).toBeDisabled();

    const rebuildButton = screen.getByRole("button", {
      name: /rebuild missing trim caches/i,
    });
    expect(rebuildButton).toBeDisabled();
    expect(rebuildButton).toHaveAttribute("title", READ_ONLY_MIRROR_MESSAGE);

    const removeButton = screen.getByRole("button", { name: "Remove shooter" });
    expect(removeButton).toBeDisabled();
    expect(removeButton).toHaveAttribute("title", READ_ONLY_MIRROR_MESSAGE);
  });

  // #836: scoreboard linking is a write that 403s on a mirror - managed
  // from the desktop install there. The CTA + its banner are hidden (not
  // disabled) when edit is denied, same reasoning as Home's help cards.
  it("hides the connect-to-scoreboard CTA and banner when edit is denied", async () => {
    renderShooters({ capabilities: ["review", "share_manage"] });
    await waitFor(() => expect(api.listMatchShooters).toHaveBeenCalled());
    await screen.findByText("Mathias");

    expect(
      screen.queryByRole("button", { name: /connect to scoreboard/i }),
    ).toBeNull();
    expect(
      screen.queryByText(/not linked to the scoreboard yet/i),
    ).toBeNull();
  });

  it("enables every affordance on a desktop-origin match with full capabilities (forward-compat)", async () => {
    renderShooters({
      origin: "desktop",
      capabilities: ["edit", "review", "share_manage"],
    });
    await waitFor(() => expect(api.listMatchShooters).toHaveBeenCalled());
    await screen.findByText("Mathias");

    expect(screen.queryByText(READ_ONLY_MIRROR_MESSAGE)).toBeNull();

    // Forward-compat: the CTA + banner reappear once edit is granted on an
    // unlinked match.
    expect(
      screen.getByRole("button", { name: /connect to scoreboard/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/not linked to the scoreboard yet/i),
    ).toBeInTheDocument();

    expect(screen.getByPlaceholderText("Johan Larsson")).toBeEnabled();
    // The manual-add button is also disabled while the name field is
    // empty (independent of capability); type a name to isolate the
    // capability gate from that separate disable reason.
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText("Johan Larsson"), "Johan");
    expect(
      screen.getByRole("button", { name: /add shooter/i }),
    ).toBeEnabled();

    const rebuildButton = screen.getByRole("button", {
      name: /rebuild missing trim caches/i,
    });
    expect(rebuildButton).toBeEnabled();
    expect(rebuildButton).not.toHaveAttribute("title", READ_ONLY_MIRROR_MESSAGE);

    const removeButton = screen.getByRole("button", { name: "Remove shooter" });
    expect(removeButton).toBeEnabled();
    expect(removeButton).toHaveAttribute("title", "Remove Mathias");
  });
});
