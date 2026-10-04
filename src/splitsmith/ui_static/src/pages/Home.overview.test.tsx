/**
 * Overview behaviour (UX PR 3): the header's primary button is the loop's
 * next step, the stats strip counts audited stages, Accept marks a stage
 * audited from the returned triage list, and a match with no footage
 * renders the empty block instead of the table.
 */
import { useEffect, useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { ConfirmProvider } from "@/components/useConfirm";
import {
  api,
  type MatchProject,
  type ShooterListEntry,
  type TriageCell,
  type TriageResponse,
} from "@/lib/api";
import { Home } from "@/pages/Home";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn(),
      getTriage: vi.fn(),
      acceptStage: vi.fn(),
      getBeepQueue: vi.fn(),
    },
  };
});
vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: vi.fn(() => ({
      mode: "hosted" as const,
      resolved: true,
    })),
  };
});

function project(): MatchProject {
  return {
    schema_version: 1,
    name: "Stockholm IPSC Open 2026",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    competitor_name: "Mathias Axell",
    scoreboard_match_id: null,
    scoreboard_content_type: null,
    selected_shooter_id: null,
    selected_competitor_id: null,
    shooter_token: null,
    match_date: "2026-06-27",
    stages: [],
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
    origin: "hosted",
  };
}
const SHOOTER: ShooterListEntry = {
  slug: "s1",
  name: "Mathias Axell",
  selected_shooter_id: null,
  selected_competitor_id: null,
  stages_audited: 1,
  stages_total: 4,
  video_count: 3,
  cameras: [],
  stages_missing_trim: 0,
  stage_statuses: [],
};

function cell(over: Partial<TriageCell>): TriageCell {
  return {
    slug: "s1",
    shooter_name: "Mathias Axell",
    stage_number: 1,
    stage_name: "Stage",
    status: "todo",
    beep_confidence: null,
    anomalies: [],
    needs_attention: null,
    video_count: 0,
    beep_time: null,
    beep_reviewed: false,
    shot_count: 0,
    draw: null,
    avg_split: null,
    time_seconds: 0,
    ...over,
  };
}
const TRIAGE: TriageResponse = {
  beep_low_confidence_threshold: 0.5,
  flagged_count: 0,
  cells: [
    cell({ stage_number: 1, stage_name: "B100 Höger", time_seconds: 48.6 }),
    cell({
      stage_number: 3,
      stage_name: "B6 Rear",
      status: "audited",
      video_count: 1,
      beep_time: 5.32,
      beep_reviewed: true,
      beep_confidence: 0.91,
      shot_count: 30,
      draw: 1.97,
      avg_split: 0.386,
      time_seconds: 32.12,
    }),
    cell({
      stage_number: 6,
      stage_name: "B5 All",
      status: "in_progress",
      video_count: 1,
      beep_time: 6.01,
      beep_reviewed: true,
      beep_confidence: 0.88,
      shot_count: 33,
      draw: 1.93,
      avg_split: 0.44,
      time_seconds: 38.2,
    }),
    cell({
      stage_number: 10,
      stage_name: "B3",
      status: "ready",
      video_count: 1,
      beep_time: 4.9,
      beep_reviewed: false,
      beep_confidence: 0.42,
      time_seconds: 24.0,
    }),
  ],
};

function OutletCtx({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}
function renderHome(
  shooters: ShooterListEntry[] = [SHOOTER],
  extra: Partial<MatchShellOutletContext> = {},
) {
  const ctx: MatchShellOutletContext = {
    project: project(),
    health: null,
    shooters,
    refresh: () => {},
    origin: "hosted",
    capabilities: ["edit", "review", "share_manage"],
    jobs: [],
    ...extra,
  };
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1"]}>
        <Routes>
          <Route path="/match/:matchId" element={<OutletCtx ctx={ctx} />}>
            <Route index element={<Home />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

describe("Overview", () => {
  beforeEach(() => {
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/r",
      match_name: "m",
      shooters: [SHOOTER],
      origin: "hosted",
      capabilities: ["edit", "review", "share_manage"],
    });
    vi.mocked(api.getTriage).mockResolvedValue(TRIAGE);
    vi.mocked(api.getBeepQueue).mockRejectedValue(
      new Error("no queue in this test"),
    );
  });

  it("when the next step is a beep, the primary opens the beep review queue at its first beep", async () => {
    const queued = (
      stage: number,
      role: "primary" | "secondary",
      videoId: string,
    ) => ({
      slug: "s1",
      shooter_name: "Shooter",
      stage_number: stage,
      stage_name: `S${stage}`,
      role,
      video_id: videoId,
      video_path: `raw/${videoId}.mp4`,
      beep_time: 4,
      beep_confidence: 0.9,
      beep_reviewed: false,
      status: "unreviewed" as const,
      alt_candidates: [],
      proxy_ready: true,
      snippet_ready: false,
      trim_stale: false,
    });
    // The queue arrives on the shell's context (the shell fetched it for
    // its badge); the Overview must not fetch it a second time (#1181).
    const beepQueue = {
      total_items: 2,
      pending_count: 2,
      confirmed_count: 0,
      origin: "hosted",
      capabilities: [],
      stages: [
        {
          stage_number: 10,
          stage_name: "B3",
          items: [
            queued(10, "secondary", "sec10"),
            queued(10, "primary", "pri10"),
          ],
        },
      ],
    } as never;
    const accepted: TriageResponse = {
      ...TRIAGE,
      cells: TRIAGE.cells.map((c) =>
        c.stage_number === 6 ? { ...c, status: "audited" } : c,
      ),
    };
    vi.mocked(api.getTriage).mockResolvedValue(accepted);
    renderHome([SHOOTER], { beepQueue });

    const primary = await screen.findByRole("link", {
      name: "Review beeps · 2 to confirm",
    });

    expect(primary).toHaveAttribute("href", "/match/m1/audit/s1/10?cam=pri10");
    expect(api.getBeepQueue).not.toHaveBeenCalled();
  });

  it("fetches the triage grid once even though the project arrives after mount (#1181)", async () => {
    // The shell's project snapshot resolves asynchronously; the grid does
    // not derive from it, so its arrival must not refetch triage.
    vi.mocked(api.getTriage).mockClear();
    vi.mocked(api.getBeepQueue).mockClear();
    function LateProject() {
      const [proj, setProj] = useState<MatchProject | null>(null);
      useEffect(() => {
        const t = setTimeout(() => setProj(project()), 20);
        return () => clearTimeout(t);
      }, []);
      const ctx: MatchShellOutletContext = {
        project: proj,
        health: null,
        shooters: [SHOOTER],
        refresh: () => {},
        origin: "hosted",
        capabilities: ["edit", "review", "share_manage"],
        jobs: [],
      };
      return <Outlet context={ctx} />;
    }
    render(
      <ConfirmProvider>
        <MemoryRouter initialEntries={["/match/m1"]}>
          <Routes>
            <Route path="/match/:matchId" element={<LateProject />}>
              <Route index element={<Home />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ConfirmProvider>,
    );

    await waitFor(() => expect(api.getTriage).toHaveBeenCalled());
    const beforeProject = vi.mocked(api.getTriage).mock.calls.length;
    // Wait for the project to have landed and rendered (the placeholder
    // goes away), then flush a tick so any effect its arrival triggered
    // has run before counting.
    await waitFor(() => expect(screen.queryByText("Reading match state...")).toBeNull());
    await new Promise((r) => setTimeout(r, 30));
    expect([beforeProject, vi.mocked(api.getTriage).mock.calls.length]).toEqual([1, 1]);
    expect(api.getBeepQueue).not.toHaveBeenCalled();
  });

  it("the header primary is the loop's next step and the stats count audited stages", async () => {
    renderHome();
    const primary = await screen.findByRole("link", {
      name: "Audit 06 B5 All",
    });
    expect(primary).toHaveAttribute("href", "/match/m1/audit/s1/6");
    expect(primary.className).toMatch(/btn-primary/);
    const strip = document.querySelector("div.grid.shrink-0") as HTMLElement;
    expect(within(strip).getByText("Audited").parentElement).toHaveTextContent(
      "1/ 4",
    );
    expect(within(strip).getByText("Avg draw").parentElement).toHaveTextContent(
      "1.97",
    );
  });

  it("Accept marks the stage audited from the returned list and moves the primary on", async () => {
    const accepted: TriageResponse = {
      ...TRIAGE,
      cells: TRIAGE.cells.map((c) =>
        c.stage_number === 6 ? { ...c, status: "audited" } : c,
      ),
    };
    vi.mocked(api.acceptStage).mockResolvedValue(accepted);
    renderHome();
    await screen.findByRole("link", { name: "Audit 06 B5 All" });
    const rows = screen.getAllByRole("row");
    await userEvent.click(
      within(rows[3]).getByRole("button", { name: /accept/i }),
    );
    expect(api.acceptStage).toHaveBeenCalledWith("s1", 6);
    expect(
      await screen.findByRole("link", { name: "Confirm beep 10 B3" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(screen.getAllByRole("row")[3]).getByText("audited"),
      ).toBeInTheDocument(),
    );
  });

  it("with everything audited the primary is Splits", async () => {
    vi.mocked(api.getTriage).mockResolvedValue({
      ...TRIAGE,
      cells: TRIAGE.cells.map((c) => ({
        ...c,
        status: "audited",
        video_count: 1,
        beep_reviewed: true,
        beep_time: 5,
      })),
    });
    renderHome();
    await screen.findByRole("table");
    const header = document.querySelector("header") as HTMLElement;
    expect(
      within(header).getByRole("link", { name: "Splits" }),
    ).toHaveAttribute("href", "/match/m1/results");
  });

  it("with no footage anywhere it renders the empty block and no table", async () => {
    vi.mocked(api.getTriage).mockResolvedValue({
      ...TRIAGE,
      cells: TRIAGE.cells.map((c) => ({
        ...c,
        status: "todo",
        video_count: 0,
        shot_count: 0,
        draw: null,
        avg_split: null,
      })),
    });
    renderHome([{ ...SHOOTER, video_count: 0 }]);
    expect(await screen.findByText(/no footage yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    // Header primary and the empty block both point at Footage.
    const links = screen.getAllByRole("link", { name: "Add footage" });
    expect(links).toHaveLength(2);
    for (const l of links)
      expect(l).toHaveAttribute("href", "/match/m1/ingest/s1");
  });
});
