import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import {
  api,
  type CoachStageResponse,
  type CoachVideoEntry,
  type CompareStageResponse,
} from "@/lib/api";

import { Compare } from "./Compare";

// vi.hoisted so the vi.mock factory (hoisted to the top of the file) can
// reference the bundle without a temporal-dead-zone error.
const bundle = vi.hoisted(
  () =>
    ({
      stage_number: 2,
      stage_name: "Standards",
      shooters: [
        {
          slug: "anna",
          name: "Anna",
          stage_time_seconds: 14.32,
          duration_seconds: 20,
          beep_offset_in_clip: 1.0,
          video_ref: "trimmed/anna.mp4",
          shots: [],
        },
        {
          slug: "bob",
          name: "Bob",
          stage_time_seconds: 15.08,
          duration_seconds: 20,
          beep_offset_in_clip: 1.2,
          video_ref: "trimmed/bob.mp4",
          shots: [],
        },
      ],
    }) as CompareStageResponse,
);

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn().mockResolvedValue({ shooters: [] }),
      getProject: vi.fn(),
      getStageCompare: vi.fn().mockResolvedValue(bundle),
      getStageCoach: vi.fn(),
      shooterVideoStreamUrl: (_slug: string, ref: string) =>
        `http://localhost/trim/${ref}`,
      videoStreamUrl: (_slug: string, path: string, kind = "auto") =>
        `http://localhost/coach/${kind}/${path}`,
    },
  };
});

// jsdom has no media playback; stub so mounting <video> never throws.
beforeAll(() => {
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
});

function makeCoachFor(
  _slug: string,
  videos: CoachVideoEntry[],
): CoachStageResponse {
  return {
    stage_number: 2,
    stage_name: "Standards",
    beep_time: 5,
    version: 0,
    videos,
    shots: [],
  };
}

function renderCompare(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/match/:matchId/compare/:stage" element={<Compare />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Compare per-shooter camera choice", () => {
  beforeAll(() => {
    vi.mocked(api.getStageCoach).mockImplementation(async (slug: string) =>
      makeCoachFor(
        slug,
        slug === "anna"
          ? [
              {
                path: "anna-primary.mp4",
                role: "primary",
                beep_in_clip: 5,
                kind: "trim" as const,
                label: "Insta360 GO 3S",
              },
              {
                path: "anna-b.mp4",
                role: "secondary",
                beep_in_clip: 9,
                kind: "trim" as const,
                label: "iPhone 17 Pro",
              },
            ]
          : [
              {
                path: "bob-primary.mp4",
                role: "primary",
                beep_in_clip: 4,
                kind: "trim" as const,
              },
            ],
      ),
    );
  });

  it("shows every angle on a multi-camera tile, named, with the count in its bar", async () => {
    renderCompare("/match/m1/compare/2");
    await screen.findByTestId("compare-page");
    const row = await screen.findByRole("group", { name: "Anna camera" });
    expect(
      within(row).getByRole("button", { name: "Insta360 GO 3S" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      within(row).getByRole("button", { name: "iPhone 17 Pro" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("2 angles")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Bob camera" })).toBeNull();
  });

  it("swaps the tile video to the chosen camera and back", async () => {
    renderCompare("/match/m1/compare/2");
    await screen.findByTestId("compare-page");
    const row = await screen.findByRole("group", { name: "Anna camera" });
    const annaVideo = () =>
      Array.from(document.querySelectorAll("video")).find(
        (v) =>
          (v as HTMLVideoElement).src.includes("anna") &&
          !v.closest("[data-testid=camera-preview]"),
      ) as HTMLVideoElement;
    expect(annaVideo().src).toContain("/trim/");
    fireEvent.click(within(row).getByRole("button", { name: "iPhone 17 Pro" }));
    expect(annaVideo().src).toBe("http://localhost/coach/trim/anna-b.mp4");
    fireEvent.click(
      within(row).getByRole("button", { name: "Insta360 GO 3S" }),
    );
    expect(annaVideo().src).toContain("/trim/");
  });

  it("previews another angle on hover, at the grid's moment", async () => {
    renderCompare("/match/m1/compare/2");
    await screen.findByTestId("compare-page");
    const row = await screen.findByRole("group", { name: "Anna camera" });
    fireEvent.mouseEnter(
      within(row).getByRole("button", { name: "iPhone 17 Pro" }),
    );
    const preview = screen.getByTestId("camera-preview");
    const video = preview.querySelector("video") as HTMLVideoElement;
    expect(video.src).toBe("http://localhost/coach/trim/anna-b.mp4");
    fireEvent.loadedMetadata(video);
    // The grid sits at the beep (time since beep 0): the camera's own beep.
    expect(video.currentTime).toBe(9);
    expect(within(preview).getByText("iPhone 17 Pro")).toBeInTheDocument();
    fireEvent.mouseLeave(row);
    expect(screen.queryByTestId("camera-preview")).toBeNull();
    // The current angle has nothing to preview.
    fireEvent.mouseEnter(
      within(row).getByRole("button", { name: "Insta360 GO 3S" }),
    );
    expect(screen.queryByTestId("camera-preview")).toBeNull();
  });

  it("applies a moment link's per-shooter camera picks", async () => {
    renderCompare("/match/m1/compare/2?t=1.00&v=anna:1");
    await screen.findByTestId("compare-page");
    const row = await screen.findByRole("group", { name: "Anna camera" });
    await waitFor(() =>
      expect(
        within(row).getByRole("button", { name: "iPhone 17 Pro" }),
      ).toHaveAttribute("aria-pressed", "true"),
    );
  });

  it("copies moment links with the current camera picks", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderCompare("/match/m1/compare/2");
    await screen.findByTestId("compare-page");
    const row = await screen.findByRole("group", { name: "Anna camera" });
    fireEvent.click(within(row).getByRole("button", { name: "iPhone 17 Pro" }));
    fireEvent.click(screen.getByRole("button", { name: /copy link/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(String(writeText.mock.calls[0][0])).toContain("v=anna%3A1");
  });
});
