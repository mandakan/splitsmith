/**
 * A corpus fixture reviewed on the phone's Audit page, as desktop Audit
 * reviews one (spec 2026-10-14): the fixture's shots, audio and source video
 * stand in for a match stage's, a shot's review flag reads beside it, and
 * Mark reviewed & next confirms it and opens the next one in the queue's
 * filter. Nothing of a match (shooters, stages, the desktop) applies.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FixtureReviewSignals, StageAudit } from "@/lib/api";
import { MobileAudit } from "@/pages/MobileAudit";

const playback = vi.hoisted(() => ({
  state: {
    playhead: 6.0,
    playing: false,
    speed: 1 as const,
    loop: null,
    playFrom: vi.fn(),
    stop: vi.fn(),
    seek: vi.fn(),
    setSpeed: vi.fn(),
    toggleLoop: vi.fn(),
    moveLoop: vi.fn(),
  },
}));
vi.mock("@/lib/useAuditPlayback", async (orig) => ({
  ...(await orig<typeof import("@/lib/useAuditPlayback")>()),
  useAuditPlayback: () => playback.state,
}));
vi.mock("@/lib/scrub-audio", () => ({ createScrubber: vi.fn(async () => null), GRAIN_S: 0.06 }));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  // No match shell around a fixture.
  useOutletContext: () => undefined,
}));

const PATH = "/repo/tests/fixtures/stage-shots-hfo-2026-stage10-s1-go3s.json";
const NEXT = "/repo/tests/fixtures/stage-shots-hfo-2026-stage11-s1-go3s.json";
const VIDEO = "/raw/VID_1.mp4";

const doc = (): StageAudit =>
  ({
    stage_number: 10,
    stage_name: "B5",
    beep_time: 5.0,
    stage_time_seconds: 20,
    fixture_window_in_source: [1.5, 40],
    anchor: { fixture_slug: "stage10-b5", snap_window_ms: 60 },
    stage_rounds: { expected: 2 },
    shots: [
      { shot_number: 1, candidate_number: 1, time: 6.0, ms_after_beep: 1000, source: "promoted", snap_displacement_ms: 58 },
      { shot_number: 2, candidate_number: 2, time: 6.5, ms_after_beep: 1500, source: "promoted", snap_displacement_ms: 3 },
    ],
    _candidates_pending_audit: {
      candidates: [
        { candidate_number: 1, time: 6.01, ms_after_beep: 1010, confidence: 0.9 },
        { candidate_number: 2, time: 6.51, ms_after_beep: 1510, confidence: 0.8 },
      ],
    },
    audit_events: [],
  }) as unknown as StageAudit;

const signals = (): FixtureReviewSignals => ({
  moves: [],
  edges: [{ time: 6.0, displacement_ms: 58 }],
  unmarked: [],
  echoes: [],
  reference: { kind: "snap", times: [6.0, 6.5] },
  rounds: 2,
});

const peaks = (bins = 400) => ({
  duration: 20,
  sample_rate: 48000,
  bins,
  peaks: Array.from({ length: bins }, () => 0.4),
  beep_time: 5.0,
  trimmed: true,
});

const apiMock = vi.hoisted(() => ({
  getStageAudit: vi.fn(),
  getStagePeaks: vi.fn(),
  saveStageAudit: vi.fn(),
  stageAudioUrl: vi.fn(() => "/audio.wav"),
  getFixtureAudit: vi.fn(),
  saveFixtureAudit: vi.fn(),
  getFixturePeaks: vi.fn(),
  getFixtureReviewSignals: vi.fn(),
  confirmReviewFixture: vi.fn(),
  getDevReviewQueue: vi.fn(),
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

function renderPage(search = "") {
  return render(
    <MemoryRouter initialEntries={[`/review/audit${search}`]}>
      <Routes>
        <Route path="/review/audit" element={<MobileAudit fixture={{ path: PATH, videoPath: VIDEO }} />} />
      </Routes>
      <Where />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  playback.state.playhead = 6.0;
  apiMock.getFixtureAudit.mockResolvedValue(doc());
  apiMock.getFixturePeaks.mockImplementation(async (_p: string, bins: number) => peaks(Math.min(bins, 400)));
  apiMock.getFixtureReviewSignals.mockResolvedValue(signals());
  apiMock.saveFixtureAudit.mockImplementation(async (_p: string, body: StageAudit) => body);
  apiMock.confirmReviewFixture.mockResolvedValue({ confirmed_at: "2026-10-14T12:00:00Z" });
  apiMock.getDevReviewQueue.mockResolvedValue({
    pending: [
      { slug: "stage-shots-other-2026-stage1-s1-go3s", audit_path: "/x.json", review_status: "needs_review" },
      { slug: "stage-shots-hfo-2026-stage10-s1-go3s", audit_path: PATH, review_status: "needs_review" },
      { slug: "stage-shots-other-2026-stage2-s1-go3s", audit_path: "/y.json", review_status: "needs_review" },
      { slug: "stage-shots-hfo-2026-stage11-s1-go3s", audit_path: NEXT, video_path: "/raw/VID_2.mp4", review_status: "needs_review" },
    ],
    flagged: [],
    done: [],
  });
});

describe("MobileAudit reviewing a fixture", () => {
  it("opens the fixture's own shots and audio, never a match stage", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "Mark reviewed & next" })).toBeInTheDocument();
    expect(apiMock.getFixtureAudit).toHaveBeenCalledWith(PATH);
    expect(apiMock.getFixturePeaks).toHaveBeenCalledWith(PATH, expect.any(Number));
    expect(apiMock.getStageAudit).not.toHaveBeenCalled();
    expect(apiMock.getStagePeaks).not.toHaveBeenCalled();
  });

  it("reads the selected shot's flag beside it, and counts against the snap", async () => {
    renderPage();
    expect(await screen.findByTestId("review-flag")).toHaveTextContent(/at the edge of its snap window/);
    expect(screen.getByTestId("fixture-sign-off")).toHaveTextContent(
      "2 shots, as snapped from the primary. The scorecard says 2 rounds.",
    );
  });

  it("Mark reviewed & next confirms it and opens the next one inside the queue's filter", async () => {
    renderPage("?q=hfo");
    fireEvent.click(await screen.findByRole("button", { name: "Mark reviewed & next" }));
    await waitFor(() =>
      expect(apiMock.confirmReviewFixture).toHaveBeenCalledWith("stage-shots-hfo-2026-stage10-s1-go3s", "audit"),
    );
    expect(apiMock.saveFixtureAudit).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(
        `/review/audit?fixture=${encodeURIComponent(NEXT)}&video=${encodeURIComponent("/raw/VID_2.mp4")}&q=hfo`,
      ),
    );
  });

  it("an edit saves through the fixture's own builder before the sign-off", async () => {
    renderPage();
    await screen.findByTestId("review-flag");
    // The selected shot (shot 1) rejected with the action area's Delete.
    fireEvent.click(screen.getByRole("button", { name: /^Delete/ }));
    fireEvent.click(screen.getByRole("button", { name: "Mark reviewed & next" }));
    await waitFor(() => expect(apiMock.saveFixtureAudit).toHaveBeenCalled());
    const [path, body] = apiMock.saveFixtureAudit.mock.calls[0] as [string, StageAudit];
    expect(path).toBe(PATH);
    expect(body.shots).toHaveLength(1);
    expect(body.shots[0]).toMatchObject({ time: 6.5, source: "promoted", snap_displacement_ms: 3 });
    expect((body as unknown as { anchor: unknown }).anchor).toEqual({ fixture_slug: "stage10-b5", snap_window_ms: 60 });
    await waitFor(() => expect(apiMock.confirmReviewFixture).toHaveBeenCalled());
  });
});
