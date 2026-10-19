/**
 * The recheck pass over secondary beeps (2026-10-14): Audit with
 * ``?recheck=secondaries`` opens the beep step on a secondary camera, walks
 * on to the next stage's secondary carrying the pass and its way back, and
 * Stop returns to the stage it started from. The band's menu starts it.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import type { BeepQueueItem, StageAudit } from "@/lib/api";
import { resetTimelinePrefsForTests } from "@/lib/timelinePrefs";
import { Audit } from "@/pages/Audit";

const ctx = vi.hoisted(() => ({
  value: {
    shooters: [{ slug: "alice", name: "Alice" }],
    origin: "local",
    capabilities: ["edit", "review"],
    jobs: [],
    refresh: vi.fn(),
  } as Record<string, unknown>,
}));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useOutletContext: () => ctx.value,
}));
vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => ({ mode: "local", resolved: true }) };
});
// The beep step's media are not what this pins.
vi.mock("@/components/audit/BeepTimeline", () => ({ BeepTimeline: () => <div data-testid="beep-timeline" /> }));
vi.mock("@/components/audit/BeepPreview", () => ({ BeepPreview: () => <div data-testid="beep-preview" /> }));

const video = (n: number, role: "primary" | "secondary") => ({
  video_id: `${role}-${n}`,
  role,
  path: `raw/${role}${n}.mp4`,
  added_at: "2026-10-01T00:00:00Z",
  beep_time: 6.0,
  aligned_time: null,
  beep_source: "manual",
  beep_confidence: 0.99,
  beep_reviewed: true,
  processed: { beep: true, shot_detect: true, trim: true },
  trim_version: "18f2a-3e8",
});

const project = () => ({
  name: "M",
  competitor_name: "Alice",
  trim_pre_buffer_seconds: 5,
  stages: [3, 4].map((n) => ({
    stage_number: n,
    stage_name: `Stage ${n}`,
    time_seconds: 4,
    videos: [video(n, "primary"), video(n, "secondary")],
  })),
});

const queueItem = (n: number, role: "primary" | "secondary"): BeepQueueItem =>
  ({
    slug: "alice",
    shooter_name: "Alice",
    stage_number: n,
    stage_name: `Stage ${n}`,
    role,
    video_id: `${role}-${n}`,
    video_path: `raw/${role}${n}.mp4`,
    beep_time: 6.0,
    aligned_time: null,
    beep_confidence: 0.99,
    beep_reviewed: true,
    status: "confirmed",
    alt_candidates: [],
    proxy_ready: true,
    snippet_ready: false,
    trim_stale: false,
  }) as BeepQueueItem;

const doc = (n: number): StageAudit => ({
  stage_number: n,
  stage_name: `Stage ${n}`,
  beep_time: 1.0,
  stage_time_seconds: 4,
  shots: [],
  audit_events: [],
});

const apiMock = vi.hoisted(() => ({
  getProject: vi.fn(),
  getAutomation: vi.fn(),
  getStageAudit: vi.fn(),
  getStagePeaks: vi.fn(),
  getBeepQueue: vi.fn(),
  listJobs: vi.fn(async () => []),
  getScrubSettings: vi.fn(async () => ({ full_res_scrub: false })),
  setScrubSettings: vi.fn(),
}));
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname + loc.search}</div>;
}

function renderAt(url: string) {
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/match/:matchId/audit/:slug/:stage" element={<Audit />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

let widthSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.clearAllMocks();
  resetTimelinePrefsForTests();
  Element.prototype.scrollIntoView = () => {};
  widthSpy = vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(1000);
  apiMock.getProject.mockResolvedValue(project());
  apiMock.getAutomation.mockResolvedValue({ settings: { beep_low_confidence_threshold: 0.97 } });
  apiMock.getStageAudit.mockImplementation(async (_s: string, n: number) => doc(n));
  apiMock.getStagePeaks.mockResolvedValue({
    duration: 10,
    sample_rate: 48000,
    bins: 40,
    peaks: Array.from({ length: 40 }, () => 0.4),
    beep_time: 1.0,
    aligned_time: null,
    trimmed: true,
  });
  apiMock.getBeepQueue.mockResolvedValue({
    total_items: 4,
    pending_count: 0,
    confirmed_count: 4,
    origin: "local",
    stages: [
      { stage_number: 3, items: [queueItem(3, "primary"), queueItem(3, "secondary")] },
      { stage_number: 4, items: [queueItem(4, "primary"), queueItem(4, "secondary")] },
    ],
  });
});

afterEach(() => {
  widthSpy.mockRestore();
});

describe("Audit's recheck pass over secondary beeps", () => {
  it("opens the secondary's beep and Looks right walks on to the next stage, carrying the pass", async () => {
    renderAt("/match/m1/audit/alice/3?cam=secondary-3&recheck=secondaries&from=alice%2F4");
    expect(await screen.findByText(/Secondary beep 1 of 2/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Looks right, next" }));
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(
        "/match/m1/audit/alice/4?cam=secondary-4&recheck=secondaries&from=alice%2F4",
      ),
    );
  });

  it("Stop goes back to the stage the pass started from", async () => {
    renderAt("/match/m1/audit/alice/3?cam=secondary-3&recheck=secondaries&from=alice%2F4");
    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/match\/m1\/audit\/alice\/4$/));
  });
});
