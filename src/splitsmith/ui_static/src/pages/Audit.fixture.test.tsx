/**
 * A corpus fixture reviewed in the Audit page (spec 2026-10-14): the
 * fixture's shots, audio and source video stand in for a match stage's; the
 * review signals are flags; Mark reviewed & next confirms it and opens the
 * next one. A snap's sign-off counts against the shots it was snapped with.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import type { FixtureReviewSignals, StageAudit } from "@/lib/api";
import { resetTimelinePrefsForTests } from "@/lib/timelinePrefs";
import { Audit } from "@/pages/Audit";

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => ({ mode: "local", resolved: true }) };
});

const PATH = "/repo/tests/fixtures/stage-shots-hfo-2026-stage10-s1-go3s.json";
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
      { shot_number: 1, candidate_number: 1, time: 6.0, ms_after_beep: 1000, source: "promoted", snap_displacement_ms: 4 },
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
  moves: [{ time: 6.0, suggested: 5.992, move_ms: -8 }],
  edges: [],
  unmarked: [],
  reference: { kind: "snap", times: [6.0, 6.5] },
  rounds: 2,
});

const peaks = () => ({
  duration: 20,
  sample_rate: 48000,
  bins: 40,
  peaks: Array.from({ length: 40 }, () => 0.4),
  beep_time: 5.0,
  trimmed: true,
});

const apiMock = vi.hoisted(() => ({
  getProject: vi.fn(),
  getStageAudit: vi.fn(),
  getStagePeaks: vi.fn(),
  getAutomation: vi.fn(),
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

function renderPage() {
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/review/audit"]}>
        <Routes>
          <Route path="/review/audit" element={<Audit fixture={{ path: PATH, videoPath: VIDEO }} />} />
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
  apiMock.getFixtureAudit.mockResolvedValue(doc());
  apiMock.getFixturePeaks.mockResolvedValue(peaks());
  apiMock.getFixtureReviewSignals.mockResolvedValue(signals());
  apiMock.saveFixtureAudit.mockImplementation(async (_p: string, body: StageAudit) => body);
  apiMock.confirmReviewFixture.mockResolvedValue({ confirmed_at: "2026-10-14T12:00:00Z" });
  apiMock.getDevReviewQueue.mockResolvedValue({
    pending: [
      { slug: "stage-shots-hfo-2026-stage10-s1-go3s", audit_path: PATH, review_status: "needs_review" },
      {
        slug: "stage-shots-hfo-2026-stage11-s1-go3s",
        audit_path: "/repo/tests/fixtures/stage-shots-hfo-2026-stage11-s1-go3s.json",
        video_path: "/raw/VID_2.mp4",
        review_status: "needs_review",
      },
    ],
    flagged: [],
    done: [],
  });
});

afterEach(() => {
  widthSpy.mockRestore();
});

describe("Audit reviewing a fixture", () => {
  it("opens the fixture, never the match's stage, with no beep step", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "Mark reviewed & next" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Re-pick beep" })).toBeNull();
    expect(apiMock.getFixtureAudit).toHaveBeenCalledWith(PATH);
    expect(apiMock.getFixturePeaks).toHaveBeenCalledWith(PATH, expect.any(Number));
    expect(apiMock.getProject).not.toHaveBeenCalled();
    expect(apiMock.getStageAudit).not.toHaveBeenCalled();
  });

  it("counts a snap against the shots it was snapped with, the rounds a note", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("fixture-sign-off")).toHaveTextContent(
        "2 shots, as snapped from the primary. The scorecard says 2 rounds.",
      ),
    );
  });

  it("flags a shot the review inventory would move", async () => {
    renderPage();
    expect((await screen.findAllByText(/Leading edge looks 8 ms earlier/)).length).toBeGreaterThan(0);
  });

  it("Mark reviewed & next confirms the fixture and opens the next one", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("fixture-sign-off")).toHaveTextContent("2 shots"), {
      timeout: 5000,
    });
    fireEvent.click(screen.getByRole("button", { name: "Mark reviewed & next" }));
    await waitFor(() =>
      expect(apiMock.confirmReviewFixture).toHaveBeenCalledWith("stage-shots-hfo-2026-stage10-s1-go3s", "audit"),
      { timeout: 5000 },
    );
    // Nothing was edited: the fixture is not rewritten.
    expect(apiMock.saveFixtureAudit).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(
      `/review/audit?fixture=${encodeURIComponent("/repo/tests/fixtures/stage-shots-hfo-2026-stage11-s1-go3s.json")}&video=${encodeURIComponent("/raw/VID_2.mp4")}`,
      ),
      { timeout: 5000 },
    );
  });

  it("is disabled until the fixture is in hand, so a save can never be built on nothing", async () => {
    let release: (d: StageAudit) => void = () => {};
    // The first read builds the page's one-stage project; the second, the
    // document the editor saves from, is held.
    apiMock.getFixtureAudit
      .mockResolvedValueOnce(doc())
      .mockImplementation(() => new Promise<StageAudit>((r) => (release = r)));
    renderPage();
    expect(await screen.findByRole("button", { name: "Mark reviewed & next" })).toBeDisabled();
    release(doc());
  });

  it("an edit saves through the fixture's own builder: the untouched snapped shot is written as it was", async () => {
    renderPage();
    await screen.findByRole("button", { name: "Mark reviewed & next" });
    await screen.findAllByText(/Leading edge looks/);
    // K on the current shot (shot 1) rejects it.
    fireEvent.keyDown(window, { key: "k" });
    fireEvent.click(screen.getByRole("button", { name: "Mark reviewed & next" }));
    await waitFor(() => expect(apiMock.saveFixtureAudit).toHaveBeenCalled(), { timeout: 5000 });
    const [path, body] = apiMock.saveFixtureAudit.mock.calls[0] as [string, StageAudit];
    expect(path).toBe(PATH);
    expect(body.shots).toHaveLength(1);
    expect(body.shots[0]).toMatchObject({ time: 6.5, source: "promoted", snap_displacement_ms: 3 });
    expect((body as unknown as { anchor: unknown }).anchor).toEqual({ fixture_slug: "stage10-b5", snap_window_ms: 60 });
  });
});
