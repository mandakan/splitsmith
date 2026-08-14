import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const apiMock = vi.hoisted(() => ({
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

beforeEach(() => {
  vi.clearAllMocks();
  playback.state.playhead = 0;
  apiMock.getStageAudit.mockResolvedValue(doc());
  apiMock.getStagePeaks.mockResolvedValue({
    duration: 22,
    sample_rate: 48000,
    bins: 8192,
    peaks: Array.from({ length: 8192 }, () => 0.4),
    beep_time: 1.0,
    trimmed: true,
  });
  apiMock.saveStageAudit.mockImplementation(async (_s: string, _n: number, p: StageAudit) => p);
});

describe("MobileAudit", () => {
  it("requests peaks at the 8192-bin cap and renders the row stack", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    expect(apiMock.getStagePeaks).toHaveBeenCalledWith("alice", 3, 8192);
  });

  it("shows the empty state when there is no audit doc", async () => {
    apiMock.getStageAudit.mockResolvedValue(null);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/nothing to audit yet/i)).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("wrapped-waveform")).toBeNull();
  });

  it("promote flips a band candidate to kept and the save carries marker_kept", async () => {
    playback.state.playhead = 3.1; // on the rejected candidate cand-3
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Promote candidate" }));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(apiMock.saveStageAudit).toHaveBeenCalled());
    const payload = apiMock.saveStageAudit.mock.calls[0][2] as StageAudit;
    expect(payload.shots.map((s) => s.candidate_number)).toContain(3);
    const kinds = (payload.audit_events ?? []).map((e) => e.kind);
    expect(kinds).toContain("marker_kept");
    expect(kinds).toContain("save");
  });

  it("nudge emits marker_time_changed and dials the readout", async () => {
    playback.state.playhead = 2.0; // on cand-1
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "+10 ms" }));
    // The nudge button itself is also labelled "+10 ms", so scope to the
    // live readout (aria-live="polite" in ActionArea) rather than
    // getByText, which would ambiguously match both.
    const readout = document.querySelector('[aria-live="polite"]');
    expect(readout?.textContent ?? "").toMatch(/\+10 ms/);
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(apiMock.saveStageAudit).toHaveBeenCalled());
    const payload = apiMock.saveStageAudit.mock.calls[0][2] as StageAudit;
    const moved = (payload.audit_events ?? []).find((e) => e.kind === "marker_time_changed");
    expect(moved?.payload).toMatchObject({ id: "cand-1", from_time: 2.0 });
    expect(payload.shots.find((s) => s.candidate_number === 1)?.time).toBeCloseTo(2.01, 3);
  });

  it("a 409 on save reloads the doc and says the stage changed elsewhere", async () => {
    const { ApiError } = await import("@/lib/api");
    playback.state.playhead = 3.1;
    apiMock.saveStageAudit.mockRejectedValue(new ApiError(409, "version_conflict"));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Promote candidate" }));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(screen.getByText(/changed elsewhere/i)).toBeInTheDocument());
    expect(apiMock.getStageAudit).toHaveBeenCalledTimes(2);
  });

  it("read-only capabilities disable save and the action area", async () => {
    ctx.value = { ...ctx.value, capabilities: ["share_manage"] };
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
    expect(screen.getByText(/read-only/)).toBeInTheDocument();
    ctx.value = { ...ctx.value, capabilities: ["edit", "review", "share_manage"] };
  });

  it("a peaks 404 names the desktop sync, not a generic failure", async () => {
    const { ApiError } = await import("@/lib/api");
    apiMock.getStagePeaks.mockRejectedValue(new ApiError(404, "not found"));
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/waiting for the desktop to sync/i)).toBeInTheDocument(),
    );
  });
});
