/**
 * MatchShell -- mobile drawer keeps the account menu (#550).
 *
 * A separate file rather than a third case in MatchShell.test.tsx's
 * "chrome ownership" describe, on purpose: that file's #663 describe
 * resolves src/lib/features.ts's module-level getServerFeatures() cache
 * to "local" mode in its own (untouched) arrangement, and that cache is
 * never invalidated -- once any test in a file resolves it, every later
 * test in the same file keeps observing it regardless of a per-test mock
 * override. Under "local" mode AccountChip self-gates to null (see
 * AccountChip.tsx), so this test would never find it there.
 * GlobalBar.hosted.test.tsx hit the identical issue and used the same
 * fix: a fresh test file gets a fresh, unpopulated module registry, which
 * sidesteps the cache without touching features.ts or any #663/#631
 * arrangement.
 */
import { useMemo, useState, type ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import {
  api,
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

import { MatchShell } from "@/components/match/MatchShell";

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

// This file only ever renders the mobile branch.
vi.mock("@/lib/useIsMobile", () => ({
  useIsMobile: () => true,
}));

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

/** MatchShell.tsx no longer renders its own header -- it portals a context
 *  row into whatever slot a ShellChromeProvider publishes (#550). Outside
 *  a provider that slot is null and the row renders nowhere, so this
 *  harness stands in for RootLayout's real slot. */
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

describe("MatchShell mobile drawer account menu (#550)", () => {
  it("still mounts the account menu inside the mobile drawer", async () => {
    stubMatchMedia();
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
      origin: "local",
      capabilities: ["edit", "review"],
    });
    vi.mocked(api.getProject).mockResolvedValue(makeProject());
    vi.mocked(api.getBeepQueue).mockResolvedValue({
      total_items: 0,
      pending_count: 0,
      confirmed_count: 0,
      stages: [],
      origin: "local",
      capabilities: ["edit", "review"],
    });
    vi.mocked(api.getTriageSummary).mockResolvedValue({ flagged_count: 0 });
    vi.mocked(api.listJobs).mockResolvedValue([]);

    renderShell();
    await userEvent.click(
      await screen.findByRole("button", { name: /open navigation/i }),
    );
    expect(await screen.findByTestId("account-chip")).toBeInTheDocument();
  });
});
