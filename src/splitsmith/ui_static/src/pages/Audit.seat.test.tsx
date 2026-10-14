/**
 * Seating on the rise foot belongs to the stage it was done on. Seat (E) and
 * Seat all look for a shot's foot from where the shot was last placed, kept
 * per marker id; candidate ids (``cand-N``) repeat from stage to stage, and
 * moving between stages does not remount the page. Kept across stages, a
 * stage-4 shot was seated from a stage-3 shot's time with the same id, seconds
 * away (2026-10-14, Hostfinalen XI stage 5).
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

const DURATION = 22;
const BEEP = 1.0;
// Two shots' bursts on both stages' audio: rise feet at 2.000 and 6.000 s.
const FEET = [2.0, 6.0];

/** Stage ``n`` with one detected shot, id ``cand-1``, 3 ms after a foot. */
const doc = (n: number, time: number): StageAudit => ({
  stage_number: n,
  stage_name: `Stage ${n}`,
  beep_time: BEEP,
  stage_time_seconds: time - BEEP,
  shots: [
    {
      shot_number: 1,
      candidate_number: 1,
      time,
      ms_after_beep: Math.round((time - BEEP) * 1000),
      source: "detected",
      id: "cand-1",
    },
  ],
  _candidates_pending_audit: {
    candidates: [{ candidate_number: 1, time, ms_after_beep: Math.round((time - BEEP) * 1000), confidence: 0.9 }],
  },
  audit_events: [],
});

const video = (n: number) => ({
  video_id: `v${n}`,
  role: "primary",
  path: `raw/stage${n}.mp4`,
  added_at: "2026-10-01T00:00:00Z",
  beep_time: 6.0,
  beep_source: "auto",
  beep_confidence: 0.99,
  beep_reviewed: true,
  processed: { beep: true, shot_detect: true, trim: true },
  trim_version: "18f2a-3e8",
});

const project = () => ({
  name: "M",
  competitor_name: "Alice",
  trim_pre_buffer_seconds: 5,
  stages: [3, 4].map((n) => ({ stage_number: n, stage_name: `Stage ${n}`, time_seconds: 4, videos: [video(n)] })),
});

/** Peaks at ``bins`` over the clip: quiet, with a 3-bin rise to each foot. */
const peaks = (bins: number) => {
  const p = Array.from({ length: bins }, () => 0.01);
  for (const foot of FEET) {
    const i = Math.round((foot / DURATION) * bins);
    [0.2, 0.6, 1.0, 0.8, 0.6, 0.4].forEach((v, k) => {
      if (i + k < bins) p[i + k] = Math.max(p[i + k], v);
    });
  }
  return { duration: DURATION, sample_rate: 48000, bins, peaks: p, beep_time: BEEP, trimmed: true };
};

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

/** Moves between stages the way the stage selector does: same mount. */
function GoToStage4() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/match/m1/audit/alice/4")}>go to stage 4</button>;
}

function renderPage() {
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1/audit/alice/3"]}>
        <GoToStage4 />
        <Routes>
          <Route path="/match/:matchId/audit/:slug/:stage" element={<Audit />} />
        </Routes>
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
  // Stage 3's cand-1 sits 3 ms after the foot at 2.0; stage 4's after 6.0.
  apiMock.getStageAudit.mockImplementation(async (_s: string, n: number) => doc(n, n === 3 ? 2.003 : 6.003));
  apiMock.getStagePeaks.mockImplementation(async (_s: string, _n: number, bins: number) => peaks(bins));
  apiMock.saveStageAudit.mockImplementation(async (_s: string, _n: number, p: StageAudit) => p);
});

afterEach(() => {
  widthSpy.mockRestore();
});

/** Waits until the page has the 1 ms peaks Seat reads, then presses E. */
async function seatCurrentShot(stage: number) {
  await waitFor(() =>
    expect(apiMock.getStagePeaks.mock.calls.some(([, n, bins]) => n === stage && bins > 1500)).toBe(true),
  );
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  fireEvent.keyDown(window, { key: "e" });
}

async function savedTime(stage: number): Promise<number> {
  fireEvent.keyDown(window, { key: "s", metaKey: true });
  await waitFor(() => expect(apiMock.saveStageAudit.mock.calls.some(([, n]) => n === stage)).toBe(true));
  const call = apiMock.saveStageAudit.mock.calls.filter(([, n]) => n === stage).at(-1)!;
  return (call[2] as StageAudit).shots[0].time;
}

describe("Seat on the rise foot", () => {
  it("seats a shot from its own stage, never from a shot with its id on the stage before", async () => {
    renderPage();
    await screen.findByText("Stage 3", { exact: false });
    await seatCurrentShot(3);
    expect(await savedTime(3)).toBeCloseTo(2.0, 3);

    fireEvent.click(screen.getByText("go to stage 4"));
    await waitFor(() => expect(apiMock.getStageAudit).toHaveBeenCalledWith("alice", 4));
    await seatCurrentShot(4);
    expect(await savedTime(4)).toBeCloseTo(6.0, 3);
  });
});
