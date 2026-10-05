import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type CoachStageResponse,
  type CoachVideoEntry,
  type CompareStageResponse,
  type MatchProject,
} from "@/lib/api";

import { Compare } from "./Compare";

function bundleFor(n: number): CompareStageResponse {
  return {
    stage_number: n,
    stage_name: `Stage ${n}`,
    shooters: ["anna", "bob"].map((slug) => ({
      slug,
      name: slug === "anna" ? "Anna" : "Bob",
      stage_time_seconds: 10,
      beep_offset_in_clip: 1,
      video_ref: `trimmed/${slug}-${n}.mp4`,
      shots: [],
    })),
  } as unknown as CompareStageResponse;
}

const cam = (
  path: string,
  role: "primary" | "secondary",
  mount: string,
  label: string,
): CoachVideoEntry => ({
  path,
  role,
  beep_in_clip: 3,
  kind: "trim",
  mount,
  label,
});

// Anna's phone is the secondary on stage 2 and the primary on stage 3: a
// choice kept by position would land on the wrong camera.
const ANNA: Record<number, CoachVideoEntry[]> = {
  2: [
    cam("anna-head-2.mp4", "primary", "head", "GO 3S"),
    cam("anna-phone-2.mp4", "secondary", "hand", "iPhone"),
  ],
  3: [
    cam("anna-phone-3.mp4", "primary", "hand", "iPhone"),
    cam("anna-head-3.mp4", "secondary", "head", "GO 3S"),
  ],
};
let saved: string | null = null;

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn(),
      getProject: vi.fn(),
      getStageCompare: vi.fn(),
      getStageCoach: vi.fn(),
      setCompareCamera: vi.fn(),
      shooterVideoStreamUrl: (_slug: string, ref: string) =>
        `http://localhost/trim/${ref}`,
      videoStreamUrl: (_slug: string, path: string, kind = "auto", _v?: string | null, stage?: number | null) =>
        `http://localhost/coach/${kind}/${path}${stage != null ? `#s${stage}` : ""}`,
    },
  };
});

beforeAll(() => {
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
});

beforeEach(() => {
  saved = null;
  vi.mocked(api.listMatchShooters).mockResolvedValue({
    shooters: [{ slug: "anna" }],
  } as never);
  vi.mocked(api.getProject).mockResolvedValue({
    stages: [2, 3].map((n) => ({ stage_number: n })),
  } as unknown as MatchProject);
  vi.mocked(api.getStageCompare).mockImplementation((n: number) =>
    Promise.resolve(bundleFor(n)),
  );
  vi.mocked(api.getStageCoach).mockImplementation(
    async (slug: string, n: number) =>
      ({
        stage_number: n,
        stage_name: `Stage ${n}`,
        beep_time: 3,
        version: 0,
        shots: [],
        videos:
          slug === "anna"
            ? ANNA[n]
            : [cam(`bob-${n}.mp4`, "primary", "head", "GO 3S")],
        compare_camera: slug === "anna" ? saved : null,
      }) as CoachStageResponse,
  );
  vi.mocked(api.setCompareCamera).mockResolvedValue({} as never);
});

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{loc.pathname + loc.search}</output>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/match/:matchId/compare/:stage"
          element={
            <>
              <Compare />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const annaPill = () => screen.findByRole("button", { name: /^Anna camera: / });

describe("Compare: the camera holds across stages", () => {
  it("a pick follows to the next stage by mount, and rides in the URL", async () => {
    renderAt("/match/m1/compare/2");
    await screen.findByText("Stage 2");
    fireEvent.click(await annaPill());
    fireEvent.click(
      within(screen.getByRole("menu")).getByRole("menuitemradio", {
        name: /iPhone/,
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent("cams=anna%3Ahand"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Next stage" }));
    await screen.findByText("Stage 3");
    // Stage 3 lists the phone first; the choice is the phone, not "camera 2".
    await waitFor(async () =>
      expect(await annaPill()).toHaveAccessibleName(
        "Anna camera: iPhone, 2 angles",
      ),
    );
    expect(screen.getByTestId("where")).toHaveTextContent(
      "/match/m1/compare/3?cams=anna%3Ahand",
    );
  });

  it("a link carrying the choice opens on it", async () => {
    renderAt("/match/m1/compare/2?cams=anna:hand");
    await screen.findByText("Stage 2");
    await waitFor(async () =>
      expect(await annaPill()).toHaveAccessibleName(
        "Anna camera: iPhone, 2 angles",
      ),
    );
  });

  it("starts on the saved default, and the owner can save the camera on screen as it", async () => {
    saved = "hand";
    renderAt("/match/m1/compare/2");
    await screen.findByText("Stage 2");
    await waitFor(async () =>
      expect(await annaPill()).toHaveAccessibleName(
        "Anna camera: iPhone, 2 angles",
      ),
    );
    fireEvent.click(await annaPill());
    const menu = screen.getByRole("menu");
    expect(
      within(menu).getByRole("menuitem", {
        name: "iPhone is the default for Anna",
      }),
    ).toBeDisabled();
    fireEvent.click(within(menu).getByRole("menuitemradio", { name: /GO 3S/ }));
    fireEvent.click(await annaPill());
    fireEvent.click(
      within(screen.getByRole("menu")).getByRole("menuitem", {
        name: "Make GO 3S the default for Anna",
      }),
    );
    // Saved by mount, not as "the primary": on stage 3 the primary is the phone.
    await waitFor(() =>
      expect(api.setCompareCamera).toHaveBeenCalledWith("anna", "head"),
    );
  });
});
