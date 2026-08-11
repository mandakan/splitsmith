/**
 * MatchShell job-settlement refetch (#663).
 *
 * The sidebar stage list renders from the shell's one-shot project
 * snapshot. These tests pin the fix: when a background job leaves the
 * active set (running -> succeeded), the shell must re-fetch the
 * project (and the beep queue) so stage status dots update without a
 * manual reload - and must NOT refetch on every poll tick while jobs
 * are still running.
 *
 * Real timers: useJobs polls at 1s while anything is active, so the
 * transition lands within ~1.2s of render.
 */
import { useMemo, useState, type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type Job,
  type MatchProject,
  type ServerHealth,
  type ShooterListEntry,
} from "@/lib/api";
import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import {
  ShellChromeProvider,
  type ShellChromeValue,
} from "@/components/layout/shellChromeContext";

import {
  MatchShell,
  toMatchRelativePath,
  viewLabelForPath,
} from "@/components/match/MatchShell";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getHealth: vi.fn(),
      getScoreboardIdentity: vi.fn(),
      getServerFeatures: vi.fn(),
      getMe: vi.fn(),
      listMatchShooters: vi.fn(),
      getProject: vi.fn(),
      getBeepQueue: vi.fn(),
      getTriageSummary: vi.fn(),
      listJobs: vi.fn(),
    },
  };
});

// Task 3's pattern: mock the viewport gate directly rather than relying on
// window.matchMedia's "matches" value, so tests can flip mobile/desktop
// per-case. Reset before every test in this file so a mobile-only test
// never leaks into the next.
const mobile = vi.hoisted(() => ({ value: false }));
vi.mock("@/lib/useIsMobile", () => ({
  useIsMobile: () => mobile.value,
}));

beforeEach(() => {
  mobile.value = false;
});

// MatchShell no longer renders its own header -- it portals a context row
// into whatever slot a ShellChromeProvider publishes (#550). Outside a
// provider that slot is null and the row renders nowhere, so every test in
// this file needs a real (DOM-attached) slot to portal into, same as
// RootLayout provides in the real app.
function ShellChromeHarness({ children }: { children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const value = useMemo<ShellChromeValue>(
    () => ({
      contextSlot: slot,
      setAccent: () => {},
      setOwnsMobileAccount: () => {},
    }),
    [slot],
  );
  return (
    <ShellChromeProvider value={value}>
      <div ref={setSlot} />
      {children}
    </ShellChromeProvider>
  );
}

function makeShooter(slug: string, name: string): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: 0,
    stages_total: 1,
    video_count: 1,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: [],
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
    origin: "local",
  };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    kind: "shot_detect",
    stage_number: 1,
    shooter_slug: "mathias",
    video_id: null,
    status: "running",
    progress: 0.5,
    message: null,
    error: null,
    cancel_requested: false,
    acknowledged: false,
    result: null,
    timings: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: null,
    ...overrides,
  };
}

const HEALTH: ServerHealth = {
  status: "ok",
  version: "0.0.0-test",
  bound: false,
  project_name: "bromma-2026",
  project_root: "/root/bromma-2026",
  match_id: "m1",
  kind: "match",
  default_shooter_slug: "mathias",
  schema_version: 1,
};

function stubMatchMedia() {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function setUpApi(listJobsImpl: () => Promise<Job[]>) {
  vi.mocked(api.getHealth).mockResolvedValue(HEALTH);
  vi.mocked(api.getScoreboardIdentity).mockResolvedValue(null);
  vi.mocked(api.getServerFeatures).mockResolvedValue({
    lab: false,
    mode: "local",
  });
  vi.mocked(api.getMe).mockResolvedValue({
    id: "local",
    email: "local@localhost",
    display_name: null,
    is_admin: false,
  });
  vi.mocked(api.listMatchShooters).mockResolvedValue({
    match_root: "/root",
    match_name: "Bromma Classic 2026",
    shooters: [makeShooter("mathias", "Mathias")],
    origin: "local",
  });
  vi.mocked(api.getProject).mockResolvedValue(makeProject());
  vi.mocked(api.getBeepQueue).mockResolvedValue({
    total_items: 0,
    pending_count: 0,
    confirmed_count: 0,
    stages: [],
    origin: "local",
  });
  vi.mocked(api.getTriageSummary).mockResolvedValue({ flagged_count: 0 });
  vi.mocked(api.listJobs).mockImplementation(listJobsImpl);
}

function renderShell() {
  return render(
    <ModeProvider>
      <AuthProvider>
        <ShellChromeHarness>
          <MemoryRouter initialEntries={["/audit/mathias/1"]}>
            <Routes>
              <Route element={<MatchShell />}>
                <Route path="/audit/:slug/:stage" element={<div>page</div>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </ShellChromeHarness>
      </AuthProvider>
    </ModeProvider>,
  );
}

/** Shared happy-path arrangement: hosted mode, one authed user, one match
 *  with one shooter, no jobs. Used by the chrome-ownership tests below and
 *  reused from the #631 mirror-banner describe (same shape, different
 *  origin per test there). */
function setUpApiWithOrigin(origin: "hosted" | "desktop" | "local") {
  vi.mocked(api.getHealth).mockResolvedValue(HEALTH);
  vi.mocked(api.getScoreboardIdentity).mockResolvedValue(null);
  vi.mocked(api.getServerFeatures).mockResolvedValue({
    lab: false,
    mode: "hosted",
  });
  vi.mocked(api.getMe).mockResolvedValue({
    id: "u1",
    email: "m@thias.se",
    display_name: null,
    is_admin: false,
  });
  vi.mocked(api.listMatchShooters).mockResolvedValue({
    match_root: "/root",
    match_name: "Bromma Classic 2026",
    shooters: [makeShooter("mathias", "Mathias")],
    origin,
  });
  vi.mocked(api.getProject).mockResolvedValue(makeProject());
  vi.mocked(api.getBeepQueue).mockResolvedValue({
    total_items: 0,
    pending_count: 0,
    confirmed_count: 0,
    stages: [],
    origin: "local",
  });
  vi.mocked(api.getTriageSummary).mockResolvedValue({ flagged_count: 0 });
  vi.mocked(api.listJobs).mockResolvedValue([]);
}

function setupHappyPath() {
  vi.clearAllMocks();
  stubMatchMedia();
  setUpApiWithOrigin("local");
}

describe("viewLabelForPath (#691)", () => {
  it('maps "/jobs" to "Jobs"', () => {
    expect(viewLabelForPath("/jobs")).toBe("Jobs");
  });

  it('maps "/beep-review" to "Beep review"', () => {
    expect(viewLabelForPath("/beep-review")).toBe("Beep review");
  });

  it('has no trailing segment for "/" or ""', () => {
    expect(viewLabelForPath("/")).toBeNull();
    expect(viewLabelForPath("")).toBeNull();
  });

  it('maps a shooter-scoped path like "/audit/anna/3" to "Audit"', () => {
    expect(viewLabelForPath("/audit/anna/3")).toBe("Audit");
  });
});

describe("toMatchRelativePath (#691)", () => {
  it("strips the /match/:matchId prefix", () => {
    expect(toMatchRelativePath("/match/m1/audit/anna/3", "m1")).toBe(
      "/audit/anna/3",
    );
  });

  it("passes the pathname through unchanged when matchId is undefined", () => {
    expect(toMatchRelativePath("/audit/anna/3", undefined)).toBe(
      "/audit/anna/3",
    );
  });

  it("passes the pathname through unchanged when it does not carry the prefix", () => {
    expect(toMatchRelativePath("/pick", "m1")).toBe("/pick");
  });
});

describe("MatchShell job settlement (#663)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stubMatchMedia();
  });

  it("refetches the project and beep queue when a job leaves the active set", async () => {
    // First poll sees the job running; every later poll sees it done.
    let polls = 0;
    setUpApi(() => {
      polls += 1;
      return Promise.resolve([
        makeJob({ status: polls === 1 ? "running" : "succeeded" }),
      ]);
    });
    renderShell();

    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
    expect(api.getProject).toHaveBeenCalledWith("mathias");
    await waitFor(() => expect(api.getBeepQueue).toHaveBeenCalledTimes(1));

    // The 1s active poll observes running -> succeeded; the shell must
    // invalidate its snapshot.
    await waitFor(
      () => expect(api.getProject).toHaveBeenCalledTimes(2),
      { timeout: 4000 },
    );
    expect(api.getProject).toHaveBeenLastCalledWith("mathias");
    await waitFor(() => expect(api.getBeepQueue).toHaveBeenCalledTimes(2));
  });

  it("does not refetch on poll ticks while jobs are still running", async () => {
    // One job settles on the second poll, another keeps running so the
    // 1s active poll keeps ticking. Exactly one refetch may happen.
    let polls = 0;
    setUpApi(() => {
      polls += 1;
      return Promise.resolve([
        makeJob({ id: "job-a", status: polls === 1 ? "running" : "succeeded" }),
        makeJob({ id: "job-b", status: "running" }),
      ]);
    });
    renderShell();

    await waitFor(
      () => expect(api.getProject).toHaveBeenCalledTimes(2),
      { timeout: 4000 },
    );
    // Let two more active-poll ticks pass; the settled job must not
    // re-trigger, and steady running jobs must not trigger at all.
    await waitFor(() => expect(polls).toBeGreaterThanOrEqual(4), {
      timeout: 4000,
    });
    expect(api.getProject).toHaveBeenCalledTimes(2);
  });
});

describe("MatchShell mirror banner (#631 Task 10)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stubMatchMedia();
  });

  it('shows the read-only mirror banner when origin is "desktop"', async () => {
    setUpApiWithOrigin("desktop");
    renderShell();

    expect(
      await screen.findByText(/synced from a desktop install/i),
    ).toBeInTheDocument();
  });

  it('does not show the mirror banner when origin is "hosted"', async () => {
    setUpApiWithOrigin("hosted");
    renderShell();

    // Wait for the shooter list to resolve so we know the origin was read.
    await waitFor(() => expect(api.listMatchShooters).toHaveBeenCalled());
    expect(
      screen.queryByText(/synced from a desktop install/i),
    ).not.toBeInTheDocument();
  });
});

describe("MatchShell chrome ownership (#550)", () => {
  beforeEach(() => {
    setupHappyPath();
  });

  it("does not render its own header element", async () => {
    renderShell();
    await screen.findByRole("navigation", { name: /breadcrumb/i });
    expect(document.querySelector("header")).toBeNull();
  });

  it("keeps the breadcrumb, shooter chips and switch project", async () => {
    renderShell();
    expect(
      await screen.findByRole("navigation", { name: /breadcrumb/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /switch project/i }),
    ).toBeInTheDocument();
  });

  // "still mounts the account menu inside the mobile drawer" lives in
  // MatchShell.mobileAccount.test.tsx, not here: it needs hosted mode for
  // AccountChip to render, but the #663 describe above already resolved
  // src/lib/features.ts's module-level getServerFeatures() cache to
  // "local" earlier in this file, and that cache is never invalidated
  // (see GlobalBar.hosted.test.tsx, which hit the identical issue). A
  // fresh file gets a fresh, unpopulated cache.
});
