/**
 * Audit's header names the model set a stage was detected with (spec
 * 2026-10-26), and says nothing for a detection from before model sets.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import type { StageAudit } from "@/lib/api";
import { resetTimelinePrefsForTests } from "@/lib/timelinePrefs";
import { Audit } from "@/pages/Audit";

const ctx = vi.hoisted(() => ({
  value: {
    shooters: [{ slug: "alice", name: "Alice" }],
    origin: "local",
    capabilities: ["edit", "review"],
    jobs: [],
    jobsState: { settled: [] },
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

const doc = (n: number): StageAudit => ({
  stage_number: n,
  stage_name: `Stage ${n}`,
  beep_time: 1,
  stage_time_seconds: 4,
  shots: [],
  _candidates_pending_audit: { candidates: [] },
  audit_events: [],
});

const project = () => ({
  name: "M",
  competitor_name: "Alice",
  trim_pre_buffer_seconds: 5,
  stages: [3].map((n) => ({
    stage_number: n,
    stage_name: `Stage ${n}`,
    time_seconds: 4,
    videos: [
      {
        video_id: "v3",
        role: "primary",
        path: "raw/stage3.mp4",
        added_at: "2026-10-01T00:00:00Z",
        beep_time: 6.0,
        beep_source: "auto",
        beep_confidence: 0.99,
        beep_reviewed: true,
        processed: { beep: true, shot_detect: true, trim: true },
        trim_version: "18f2a-3e8",
      },
    ],
  })),
});

const apiMock = vi.hoisted(() => ({
  getProject: vi.fn(),
  getAutomation: vi.fn(),
  getStageAudit: vi.fn(),
  getStagePeaks: vi.fn(),
  saveStageAudit: vi.fn(),
  listJobs: vi.fn(async () => []),
  getScrubSettings: vi.fn(async () => ({ full_res_scrub: false })),
  setScrubSettings: vi.fn(),
}));
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

const tree = () => (
  <ConfirmProvider>
    <MemoryRouter initialEntries={["/match/m1/audit/alice/3"]}>
      <Routes>
        <Route path="/match/:matchId/audit/:slug/:stage" element={<Audit />} />
      </Routes>
    </MemoryRouter>
  </ConfirmProvider>
);

beforeEach(() => {
  vi.clearAllMocks();
  resetTimelinePrefsForTests();
  Element.prototype.scrollIntoView = () => {};
  ctx.value.jobsState = { settled: [] };
  apiMock.getProject.mockResolvedValue(project());
  apiMock.getAutomation.mockResolvedValue({ settings: { beep_low_confidence_threshold: 0.97 } });
  apiMock.getStageAudit.mockImplementation(async (_s: string, n: number) => doc(n));
  apiMock.getStagePeaks.mockResolvedValue({ duration: 10, sample_rate: 48000, bins: 100, peaks: [], beep_time: 1, trimmed: true });
});

describe("Audit names the model a stage was detected with", () => {
  it("shows the model set the detection ran with", async () => {
    apiMock.getStageAudit.mockImplementation(async (_s: string, n: number) => ({
      ...doc(n),
      _candidates_pending_audit: { candidates: [], _model_set: "base", _calibration_built_at: "2026-10-26T00:00:00Z" },
    }));
    render(tree());
    expect(await screen.findByText("Detected without the audio model")).toBeInTheDocument();
  });

  it("says nothing for a detection from before model sets", async () => {
    render(tree());
    await screen.findByText("Stage 3", { exact: false });
    await waitFor(() => expect(apiMock.getStageAudit).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/detected with/i)).toBeNull();
  });
});
