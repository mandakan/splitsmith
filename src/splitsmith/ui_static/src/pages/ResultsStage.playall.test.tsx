/**
 * Play all - one shooter's audited stages back to back.
 *
 * The page owns the ``?play=all`` flag and the advance; the player only
 * reports the window end. Covered here: the toggle round-trips the
 * query, a window end under play-all navigates to the next audited
 * stage (keeping the flag, keeping the share prefix) and that stage
 * autoplays, the last stage just stops, and without the flag nothing
 * changes.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import type { CoachShot, CoachStageResponse, ShooterListEntry, StageStatus } from "@/lib/api";

import { ResultsStage } from "@/pages/ResultsStage";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getStageCoach: vi.fn(),
      getProject: vi.fn().mockRejectedValue(new Error("no project")),
      getMatchCoachDistributions: vi.fn().mockRejectedValue(new Error("no dist")),
      listStageComments: vi.fn().mockResolvedValue({ comments: [] }),
      videoStreamUrl: () => "http://localhost/video.mp4",
    },
  };
});

import { api } from "@/lib/api";

beforeAll(() => {
  // SplitsList scrolls the active row into view once playback passes a
  // shot; jsdom has no layout and no scrollIntoView.
  Element.prototype.scrollIntoView = () => {};
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  window.matchMedia = ((query: string) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

// beep 5, one shot at +7 -> window [2, 15].
const SHOT: CoachShot = {
  id: "cand-1",
  shot_number: 1,
  ms_after_beep: 7000,
  time_from_beep: 7,
  time_absolute: 12,
  split: 7,
  interval_class: "split",
  interval_class_source: "auto",
  improvement_flag: false,
  coaching_note: null,
  stale: false,
  reload_hint: false,
};

function makeCoach(cameras = 1): CoachStageResponse {
  return {
    stage_number: 1,
    stage_name: "Steel Rush",
    beep_time: 5,
    version: 1,
    videos: Array.from({ length: cameras }, (_, i) => ({
      path: `trimmed/stage-cam${i}.mp4`,
      role: i === 0 ? ("primary" as const) : ("secondary" as const),
      beep_in_clip: 5,
      kind: "trim" as const,
    })),
    shots: [SHOT],
  };
}

function makeShooter(slug: string, statuses: [number, StageStatus][]): ShooterListEntry {
  return {
    slug,
    name: slug,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: statuses.filter(([, s]) => s === "audited").length,
    stages_total: statuses.length,
    video_count: 0,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: statuses.map(([stage_number, status]) => ({ stage_number, status })),
  };
}

const SHOOTERS = [
  makeShooter("anna", [
    [1, "audited"],
    [2, "ready"],
    [3, "audited"],
  ]),
];

function Shell({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function LocationSpy() {
  const loc = useLocation();
  return <output data-testid="loc">{loc.pathname + loc.search}</output>;
}

function renderStage(path: string, opts: { cameras?: number; state?: unknown } = {}) {
  vi.mocked(api.getStageCoach).mockResolvedValue(makeCoach(opts.cameras));
  const [pathname, search = ""] = path.split("?");
  const entry = { pathname, search: search ? `?${search}` : "", state: opts.state ?? null };
  const ctx: MatchShellOutletContext = {
    project: null,
    health: null,
    shooters: SHOOTERS,
    refresh: vi.fn(),
    origin: null,
    capabilities: null,
  };
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <LocationSpy />
      <Routes>
        <Route element={<Shell ctx={ctx} />}>
          <Route path="/match/:matchId/results/:slug/:stage" element={<ResultsStage />} />
          <Route path="/share/:token/results/:slug/:stage" element={<ResultsStage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

const loc = () => screen.getByTestId("loc").textContent;

/** Wait for the coach to load, then give the mounted <video> the
 *  play/pause/paused behaviour jsdom lacks. */
async function armVideo(container: HTMLElement) {
  await screen.findByRole("button", { name: "Play" });
  const video = container.querySelector("video")!;
  let paused = true;
  Object.defineProperty(video, "paused", { get: () => paused, configurable: true });
  const play = vi.fn(() => {
    paused = false;
    return Promise.resolve();
  });
  const pause = vi.fn(() => {
    paused = true;
  });
  video.play = play;
  video.pause = pause;
  Object.defineProperty(video, "duration", { value: 60, configurable: true });
  fireEvent(video, new Event("loadedmetadata"));
  return { video, play, pause };
}

function playToWindowEnd(video: HTMLVideoElement) {
  void video.play();
  fireEvent(video, new Event("play"));
  video.currentTime = 15.3;
  fireEvent(video, new Event("timeupdate"));
}

describe("ResultsStage play all", () => {
  it("toggles ?play=all from the header button, in place", async () => {
    renderStage("/match/m1/results/anna/1");
    const toggle = await screen.findByRole("button", { name: /play all/i });
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(toggle);
    expect(loc()).toBe("/match/m1/results/anna/1?play=all");
    expect(screen.getByRole("button", { name: /play all/i })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: /play all/i }));
    expect(loc()).toBe("/match/m1/results/anna/1");
  });

  it("advances to the next audited stage at the window end and autoplays it", async () => {
    const { container } = renderStage("/match/m1/results/anna/1?play=all");
    const first = await armVideo(container);

    playToWindowEnd(first.video);
    expect(first.pause).toHaveBeenCalledTimes(1);
    // Stage 2 is only "ready" - the next *audited* stage is 3.
    await waitFor(() => expect(loc()).toBe("/match/m1/results/anna/3?play=all"));

    const second = await armVideo(container);
    expect(second.video).not.toBe(first.video);
    expect(second.play).toHaveBeenCalledTimes(1);
  });

  it("keeps the share prefix when advancing on the anonymous surface", async () => {
    const { container } = renderStage("/share/tok/results/anna/1?play=all");
    const { video } = await armVideo(container);
    playToWindowEnd(video);
    await waitFor(() => expect(loc()).toBe("/share/tok/results/anna/3?play=all"));
  });

  it("stops on the last audited stage", async () => {
    const { container } = renderStage("/match/m1/results/anna/3?play=all");
    const { video, pause } = await armVideo(container);
    playToWindowEnd(video);
    expect(pause).toHaveBeenCalledTimes(1);
    await new Promise((r) => setTimeout(r, 0));
    expect(loc()).toBe("/match/m1/results/anna/3?play=all");
  });

  it("neither stops nor advances without the flag", async () => {
    const { container } = renderStage("/match/m1/results/anna/1");
    const { video, pause } = await armVideo(container);
    playToWindowEnd(video);
    await new Promise((r) => setTimeout(r, 0));
    expect(pause).not.toHaveBeenCalled();
    expect(loc()).toBe("/match/m1/results/anna/1");
  });

  it("autoplays only the first player mount - a camera switch keeps the viewer's own play state", async () => {
    const { container } = renderStage("/match/m1/results/anna/1?play=all", {
      cameras: 2,
      state: { autoplay: true },
    });
    const first = await armVideo(container);
    expect(first.play).toHaveBeenCalledTimes(1);
    // Viewer pauses, then switches camera: the remounted player must not
    // start playing on its own.
    first.pause();
    fireEvent(first.video, new Event("pause"));
    fireEvent.click(screen.getByRole("button", { name: /camera 2 of 2/i }));
    const second = await armVideo(container);
    expect(second.video).not.toBe(first.video);
    expect(second.play).not.toHaveBeenCalled();
  });

  it("does not autoplay a play-all link opened cold", async () => {
    const { container } = renderStage("/match/m1/results/anna/1?play=all");
    const { play } = await armVideo(container);
    expect(play).not.toHaveBeenCalled();
  });
});
