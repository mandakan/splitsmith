/**
 * MatchShell sidebar shortcut (#1352, shared-timeline Task 7).
 *
 * Cmd/Ctrl+B toggles the match sidebar's collapsed state, freeing width
 * for the full-width timeline band. It must not fire while a text input
 * is focused (``isTypingTextTarget``), nor with Alt or Shift held, and it
 * must preventDefault so the browser's own Cmd/Ctrl+B never fires.
 *
 * Harness copied from MatchShell.test.tsx (its helpers aren't exported);
 * kept to the minimum this test needs.
 */
import { useMemo, useState, type ReactNode } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

// MatchShell portals its context row into a ShellChromeProvider slot;
// outside a provider the slot is null, same as MatchShell.test.tsx's
// harness.
function ShellChromeHarness({ children }: { children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [crumb, setCrumb] = useState<HTMLElement | null>(null);
  const value = useMemo<ShellChromeValue>(
    () => ({
      contextSlot: slot,
      stripSlot: null,
      crumbSlot: crumb,
      setAccent: () => {},
      setOwnsMobileAccount: () => {},
    }),
    [slot, crumb],
  );
  return (
    <ShellChromeProvider value={value}>
      <div ref={setCrumb} data-testid="crumb-slot" />
      <div ref={setSlot} data-testid="context-slot" />
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

function setUpApi() {
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
    access_tier: "full",
    features: ["create_match", "hosted_compute", "raw_upload", "share", "sync"],
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

describe("MatchShell sidebar shortcut (#1352)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stubMatchMedia();
    setUpApi();
  });

  it("Cmd/Ctrl+B collapses and expands the sidebar, except while typing", async () => {
    renderShell();
    const sidebar = await screen.findByRole("button", { name: "Collapse sidebar" });
    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "b", ctrlKey: true });
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
    input.remove();
    fireEvent.keyDown(window, { key: "b", metaKey: true });
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeInTheDocument();
    expect(sidebar).toBeDefined();
  });

  it("does not fire with Alt or Shift held, and preventDefaults when it does", async () => {
    renderShell();
    await screen.findByRole("button", { name: "Collapse sidebar" });

    fireEvent.keyDown(window, { key: "b", ctrlKey: true, altKey: true });
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "b", ctrlKey: true, shiftKey: true });
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeInTheDocument();

    const event = new KeyboardEvent("keydown", {
      key: "b",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});
