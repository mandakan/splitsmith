import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import type {
  CoachShot,
  CoachStageResponse,
  CoachVideoEntry,
  ShooterListEntry,
  StageStatus,
} from "@/lib/api";

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
      patchStageShotCoach: vi.fn(),
      videoStreamUrl: (_slug: string, path: string, kind = "auto", _v?: string | null, stage?: number | null) =>
        `http://localhost/${kind}/${path}${stage != null ? `#s${stage}` : ""}`,
    },
  };
});

import { api } from "@/lib/api";

beforeAll(() => {
  // jsdom lacks both; ResultsStage measures the player box and the
  // ShotTicker probes prefers-reduced-motion.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  // PipView lays the inset over the video's measured frame; jsdom
  // measures nothing, so every box is 800 x 450.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () =>
      ({ left: 0, top: 0, right: 800, bottom: 450, width: 800, height: 450, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect,
  );
  window.matchMedia = ((query: string) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

function makeCoach(videos: CoachVideoEntry[], shots: CoachShot[] = []): CoachStageResponse {
  return { stage_number: 2, stage_name: "Steel Rush", beep_time: 5, version: 4, videos, shots };
}

function makeShooter(
  slug: string,
  name: string,
  statuses: [number, StageStatus][],
): ShooterListEntry {
  return {
    slug,
    name,
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

function Shell({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderStage(
  path: string,
  shooters: ShooterListEntry[],
  opts: { videos: CoachVideoEntry[]; shots?: CoachShot[]; compareCamera?: string },
) {
  vi.mocked(api.getStageCoach).mockResolvedValue({
    ...makeCoach(opts.videos, opts.shots ?? []),
    compare_camera: opts.compareCamera ?? null,
  });
  const ctx: MatchShellOutletContext = {
    project: null,
    health: null,
    shooters,
    refresh: vi.fn(),
    origin: null,
    capabilities: null,
  };
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Shell ctx={ctx} />}>
          <Route path="/match/:matchId/results/:slug/:stage" element={<ResultsStage />} />
          <Route path="/share/:token/results/:slug/:stage" element={<ResultsStage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

const TWO_CAMS: CoachVideoEntry[] = [
  { path: "cam-primary.mp4", role: "primary", beep_in_clip: 5, kind: "trim" as const },
  { path: "cam-b.mp4", role: "secondary", beep_in_clip: 12, kind: "trim" as const },
];

function mainVideoSrcs(): string[] {
  // The PiP inset's video is aria-hidden; the main player's video is not.
  return Array.from(document.querySelectorAll("video:not([aria-hidden])")).map(
    (v) => (v as HTMLVideoElement).src,
  );
}

function insetSrc(): string | null {
  return (screen.queryByTestId("pip-inset-video") as HTMLVideoElement | null)?.src ?? null;
}

const swap = () => fireEvent.click(screen.getByRole("button", { name: /swap with the big camera/i }));

describe("ResultsStage cameras (PiP)", () => {
  it("renders no inset for a single-camera run", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: [TWO_CAMS[0]],
    });
    await screen.findByText(/steel rush/i);
    expect(screen.queryByTestId("pip-view")).toBeNull();
    expect(screen.queryByTestId("pip-inset")).toBeNull();
  });

  it("shows the other camera as the inset and swaps without remounting the player", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: TWO_CAMS,
    });
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/trim/cam-primary.mp4#s2"]);
    expect(insetSrc()).toBe("http://localhost/trim/cam-b.mp4#s2");
    const player = document.querySelector("video:not([aria-hidden])");
    swap();
    expect(mainVideoSrcs()).toEqual(["http://localhost/trim/cam-b.mp4#s2"]);
    expect(insetSrc()).toBe("http://localhost/trim/cam-primary.mp4#s2");
    expect(document.querySelector("video:not([aria-hidden])")).toBe(player);
  });

  it("the inset streams the rendition when the camera has one", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: [TWO_CAMS[0], { ...TWO_CAMS[1], scrub_version: "r1" }],
    });
    await screen.findByText(/steel rush/i);
    expect(insetSrc()).toBe("http://localhost/scrub/cam-b.mp4#s2");
    // The rendition failed: the trim it was cut from, same beep anchor.
    fireEvent.error(screen.getByTestId("pip-inset-video"));
    await waitFor(() => expect(insetSrc()).toBe("http://localhost/trim/cam-b.mp4#s2"));
  });

  it("C swaps the cameras, never while typing", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: TWO_CAMS,
    });
    await screen.findByText(/steel rush/i);
    const typing = document.createElement("textarea");
    document.body.appendChild(typing);
    fireEvent.keyDown(typing, { key: "c" });
    expect(mainVideoSrcs()).toEqual(["http://localhost/trim/cam-primary.mp4#s2"]);
    typing.remove();
    fireEvent.keyDown(document.body, { key: "c" });
    expect(mainVideoSrcs()).toEqual(["http://localhost/trim/cam-b.mp4#s2"]);
    fireEvent.keyDown(document.body, { key: "C", shiftKey: true });
    expect(mainVideoSrcs()).toEqual(["http://localhost/trim/cam-primary.mp4#s2"]);
  });

  it("plays the primary's audio while a secondary is big", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: TWO_CAMS,
    });
    await screen.findByText(/steel rush/i);
    const made: HTMLAudioElement[] = [];
    const RealAudio = window.Audio;
    window.Audio = function FakeAudio() {
      const a = document.createElement("audio");
      made.push(a);
      return a;
    } as unknown as typeof Audio;
    try {
      const player = document.querySelector("video:not([aria-hidden])") as HTMLVideoElement;
      expect(made).toHaveLength(0);
      expect(player.muted).toBe(false);
      swap();
      expect(made).toHaveLength(1);
      // The primary's own stream; its beep anchor is its own beep_in_clip.
      expect(made[0].src).toBe("http://localhost/trim/cam-primary.mp4#s2");
      expect(player.muted).toBe(true);
      expect(made[0].muted).toBe(false);
      swap();
      expect(player.muted).toBe(false);
      expect(made[0].getAttribute("src")).toBeNull();
    } finally {
      window.Audio = RealAudio;
    }
  });

  it("opens on the camera a moment link names via ?v=", async () => {
    renderStage(
      "/match/m1/results/anna/2?t=1.00&v=1",
      [makeShooter("anna", "Anna", [[2, "audited"]])],
      { videos: TWO_CAMS },
    );
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/trim/cam-b.mp4#s2"]);
  });

  it("falls back to the first camera when no primary exists", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: [
        { path: "cam-b.mp4", role: "secondary", beep_in_clip: 12, kind: "source" as const },
      ],
    });
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/source/cam-b.mp4#s2"]);
  });

  it("copies a moment link anchored to the active camera's own beep, not the primary's", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: TWO_CAMS,
    });
    await screen.findByText(/steel rush/i);

    swap();
    expect(mainVideoSrcs()).toEqual(["http://localhost/trim/cam-b.mp4#s2"]);

    // Cam B's beep_in_clip is 12 - park the video 3s past it on cam B's
    // own clock, so the correct t (seconds after beep, camera-independent)
    // is a deterministic 3.00, not 15 - coach.beep_time (5) = 10.
    const video = document.querySelector("video:not([aria-hidden])") as HTMLVideoElement;
    video.currentTime = 15;

    fireEvent.click(screen.getByRole("button", { name: /copy link at moment/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));

    const url = new URL(writeText.mock.calls[0][0] as string);
    expect(url.searchParams.get("v")).toBe("1");
    expect(url.searchParams.get("t")).toBe("3.00");
  });

  it("share mount: a ?v= moment link opens on the named camera", async () => {
    renderStage(
      "/share/tok123/results/anna/2?t=1.00&v=1",
      [makeShooter("anna", "Anna", [[2, "audited"]])],
      { videos: TWO_CAMS },
    );
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/trim/cam-b.mp4#s2"]);
    // The primary is the inset, through the same scoped stream URL.
    expect(insetSrc()).toBe("http://localhost/trim/cam-primary.mp4#s2");
  });

  describe("the camera holds across stages", () => {
    const HEAD_THEN_PHONE: CoachVideoEntry[] = [
      { path: "head.mp4", role: "primary", beep_in_clip: 5, kind: "trim" as const, mount: "head" },
      { path: "phone.mp4", role: "secondary", beep_in_clip: 12, kind: "trim" as const, mount: "hand" },
    ];
    const anna = [makeShooter("anna", "Anna", [[2, "audited"], [3, "audited"]])];

    it("opens on the camera chosen on an earlier stage (?cams=)", async () => {
      renderStage("/match/m1/results/anna/2?cams=anna:hand", anna, { videos: HEAD_THEN_PHONE });
      await screen.findByText(/steel rush/i);
      expect(mainVideoSrcs()).toEqual(["http://localhost/trim/phone.mp4#s2"]);
    });

    it("opens on the shooter's saved default when nothing was chosen", async () => {
      renderStage("/match/m1/results/anna/2", anna, { videos: HEAD_THEN_PHONE, compareCamera: "hand" });
      await screen.findByText(/steel rush/i);
      await waitFor(() => expect(mainVideoSrcs()).toEqual(["http://localhost/trim/phone.mp4#s2"]));
    });

    it("a pick rides on to the next stage", async () => {
      renderStage("/match/m1/results/anna/2", anna, { videos: HEAD_THEN_PHONE });
      await screen.findByText(/steel rush/i);
      expect(screen.getByRole("link", { name: "Next stage" }).getAttribute("href")).not.toContain("cams=");
      swap();
      await waitFor(() =>
        expect(screen.getByRole("link", { name: "Next stage" })).toHaveAttribute(
          "href",
          "/match/m1/results/anna/3?cams=anna%3Ahand",
        ),
      );
    });
  });
});

