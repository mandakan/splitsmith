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
      videoStreamUrl: (_slug: string, path: string, kind = "auto", _v?: string | null, stage?: number | null) =>
        `http://localhost/coach/${kind}/${path}${stage != null ? `#s${stage}` : ""}`,
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

  const trigger = () => screen.findByRole("button", { name: /^Anna camera: / });
  const tileVideo = () =>
    Array.from(document.querySelectorAll("video")).find(
      (v) =>
        (v as HTMLVideoElement).src.includes("anna") &&
        !v.closest("[role=menu]"),
    ) as HTMLVideoElement;

  it("names the camera on screen and the angle count in the tile's name bar, only for multi-camera shooters", async () => {
    renderCompare("/match/m1/compare/2");
    await screen.findByTestId("compare-page");
    const pill = await trigger();
    expect(pill).toHaveAccessibleName("Anna camera: Insta360 GO 3S, 2 angles");
    expect(pill).toHaveTextContent("Insta360 GO 3S· 2 angles");
    expect(screen.queryByRole("button", { name: /^Bob camera: / })).toBeNull();
    // Nothing sits on the picture any more.
    expect(screen.queryByRole("group", { name: "Anna camera" })).toBeNull();
  });

  it("the menu lists every angle with a still at the grid's moment, and switches the tile", async () => {
    renderCompare("/match/m1/compare/2");
    await screen.findByTestId("compare-page");
    fireEvent.click(await trigger());
    const menu = screen.getByRole("menu");
    const items = within(menu).getAllByRole("menuitemradio");
    expect(items.map((i) => i.getAttribute("aria-checked"))).toEqual([
      "true",
      "false",
    ]);
    const still = within(items[1]).getByTestId(
      "camera-preview",
    ) as HTMLVideoElement;
    expect(still.src).toBe("http://localhost/coach/trim/anna-b.mp4#s2");
    fireEvent.loadedMetadata(still);
    // The grid sits at the beep (time since beep 0): the camera's own beep.
    expect(still.currentTime).toBe(9);

    expect(tileVideo().src).toContain("/trim/");
    fireEvent.click(items[1]);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(tileVideo().src).toBe("http://localhost/coach/trim/anna-b.mp4#s2");
    expect(await trigger()).toHaveAccessibleName(
      "Anna camera: iPhone 17 Pro, 2 angles",
    );
  });

  it("applies a moment link's per-shooter camera picks", async () => {
    renderCompare("/match/m1/compare/2?t=1.00&v=anna:1");
    await screen.findByTestId("compare-page");
    await waitFor(async () =>
      expect(await trigger()).toHaveAccessibleName(
        "Anna camera: iPhone 17 Pro, 2 angles",
      ),
    );
  });

  it("copies moment links with the current camera picks", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderCompare("/match/m1/compare/2");
    await screen.findByTestId("compare-page");
    fireEvent.click(await trigger());
    fireEvent.click(
      within(screen.getByRole("menu")).getAllByRole("menuitemradio")[1],
    );
    fireEvent.click(screen.getByRole("button", { name: /copy link/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(String(writeText.mock.calls[0][0])).toContain("v=anna%3A1");
  });
});
