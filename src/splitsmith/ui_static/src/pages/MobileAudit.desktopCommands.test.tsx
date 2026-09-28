/**
 * Desktop requests on the phone's Audit (#1100 S3): on a desktop-synced
 * match the phone asks the desktop to re-detect a stage instead of
 * running detection itself, and shows what the desktop is doing with it.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StageAudit } from "@/lib/api";
import { MobileAudit } from "@/pages/MobileAudit";

const playback = vi.hoisted(() => ({
  state: {
    playhead: 0,
    playing: false,
    speed: 1 as const,
    loop: null,
    playFrom: vi.fn(),
    stop: vi.fn(),
    seek: vi.fn(),
    setSpeed: vi.fn(),
    toggleLoop: vi.fn(),
  },
}));
vi.mock("@/lib/useAuditPlayback", async (orig) => ({
  ...(await orig<typeof import("@/lib/useAuditPlayback")>()),
  useAuditPlayback: () => playback.state,
}));
vi.mock("@/lib/scrub-audio", () => ({
  createScrubber: vi.fn(async () => null),
  GRAIN_S: 0.06,
}));

const ctx = vi.hoisted(() => ({
  value: {
    project: null,
    origin: "hosted",
    capabilities: ["edit", "review", "share_manage"],
    refresh: vi.fn(),
  } as Record<string, unknown>,
}));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useOutletContext: () => ctx.value,
}));

const doc = (): StageAudit => ({
  stage_number: 3,
  stage_name: "Stage 3",
  beep_time: 1.0,
  stage_time_seconds: 20.5,
  shots: [
    { shot_number: 1, candidate_number: 1, time: 2.0, ms_after_beep: 1000, source: "detected", id: "cand-1" },
    { shot_number: 2, candidate_number: 2, time: 2.4, ms_after_beep: 1400, source: "detected", id: "cand-2" },
  ],
  _candidates_pending_audit: {
    candidates: [
      { candidate_number: 1, time: 2.0, ms_after_beep: 1000, confidence: 0.9 },
      { candidate_number: 2, time: 2.4, ms_after_beep: 1400, confidence: 0.8 },
      { candidate_number: 3, time: 3.1, ms_after_beep: 2100, confidence: 0.1 },
    ],
  },
  audit_events: [],
});

const projectWithVideo = () => ({
  stages: [
    {
      stage_number: 3,
      videos: [
        {
          role: "primary",
          path: "raw/stage3.mp4",
          processed: { beep: true, shot_detect: false, trim: false },
        },
      ],
    },
  ],
});

// WrappedWaveform maps one SVG node per peak bin, so render cost is linear in
// bins. Only the cap test needs the production PEAKS_BINS value; every other
// test renders the same component and asserts nothing about bin geometry, so
// it gets a small fixture (11 rows x 40 bins).
const PEAKS_SMALL = 440;

const peaksResult = (bins: number) => ({
  duration: 22,
  sample_rate: 48000,
  bins,
  peaks: Array.from({ length: bins }, () => 0.4),
  beep_time: 1.0,
  trimmed: true,
});

const apiMock = vi.hoisted(() => ({
  listDesktopCommands: vi.fn(),
  requestDesktopCommand: vi.fn(),
  cancelDesktopCommand: vi.fn(),
  getStageAudit: vi.fn(),
  getStagePeaks: vi.fn(),
  saveStageAudit: vi.fn(),
  stageAudioUrl: vi.fn(() => "/audio.wav"),
  videoStreamUrl: vi.fn(() => "/video.mp4"),
}));
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/match/m1/audit/alice/3"]}>
      <Routes>
        <Route path="/match/:matchId/audit/:slug/:stage" element={<MobileAudit />} />
      </Routes>
    </MemoryRouter>,
  );
}

function command(over: Record<string, unknown> = {}) {
  return {
    id: "c1",
    match_id: "m1",
    kind: "shot_detect",
    slug: "alice",
    stage_number: 3,
    args: { reset: true },
    expected_revision: null,
    status: "pending",
    cancel_requested: false,
    progress_message: null,
    error: null,
    result: null,
    requested_at: "2026-09-28T11:59:00Z",
    claimed_at: null,
    lease_expires_at: null,
    finished_at: null,
    ...over,
  };
}

const AROUND = { linked: true, last_seen_at: new Date().toISOString(), around: true };

function mirror() {
  ctx.value = {
    project: projectWithVideo(),
    origin: "desktop",
    capabilities: ["review", "share_manage", "comment_write"],
    refresh: vi.fn(),
  };
}

describe("MobileAudit desktop requests", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.getStageAudit.mockResolvedValue(doc());
    apiMock.getStagePeaks.mockResolvedValue(peaksResult(PEAKS_SMALL));
    apiMock.listDesktopCommands.mockResolvedValue({ commands: [], presence: AROUND });
  });

  it("offers nothing on a hosted-native match, and asks nothing", async () => {
    ctx.value = { project: projectWithVideo(), origin: "hosted", capabilities: ["edit", "review"], refresh: vi.fn() };
    renderPage();
    await screen.findByRole("button", { name: /save/i });
    expect(screen.queryByRole("button", { name: "Stage actions" })).not.toBeInTheDocument();
    expect(apiMock.listDesktopCommands).not.toHaveBeenCalled();
  });

  it("asks the desktop from the stage menu, after a confirm, and shows the wait", async () => {
    mirror();
    apiMock.requestDesktopCommand.mockResolvedValue(command());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Stage actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /re-detect on desktop/i }));
    expect(apiMock.requestDesktopCommand).not.toHaveBeenCalled();
    apiMock.listDesktopCommands.mockResolvedValue({ commands: [command()], presence: AROUND });
    fireEvent.click(await screen.findByRole("button", { name: "Re-detect" }));
    await waitFor(() =>
      expect(apiMock.requestDesktopCommand).toHaveBeenCalledWith({
        kind: "shot_detect",
        slug: "alice",
        stage_number: 3,
      }),
    );
    expect(await screen.findByText("Your desktop will pick this up shortly.")).toBeInTheDocument();
  });

  it("offers detection on the desktop when the stage has nothing to audit", async () => {
    mirror();
    apiMock.getStageAudit.mockResolvedValue(null);
    renderPage();
    expect(await screen.findByRole("button", { name: "Detect on desktop" })).toBeInTheDocument();
  });

  it("reloads the stage when the desktop's request succeeds", async () => {
    mirror();
    apiMock.listDesktopCommands
      .mockResolvedValueOnce({ commands: [command({ status: "claimed" })], presence: AROUND })
      .mockResolvedValue({
        commands: [command({ status: "succeeded", finished_at: new Date().toISOString() })],
        presence: AROUND,
      });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderPage();
      await screen.findByText(/running on your desktop/i);
      const loads = apiMock.getStageAudit.mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      await screen.findByText(/re-detected on your desktop/i, undefined, { timeout: 5000 });
      await waitFor(() => expect(apiMock.getStageAudit.mock.calls.length).toBeGreaterThan(loads), {
        timeout: 5000,
      });
    } finally {
      vi.useRealTimers();
    }
  });
});
