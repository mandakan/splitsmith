import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { flushSync } from "react-dom";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { CoachShot, CoachStageResponse, StageEvent } from "@/lib/api";
import {
  BAND_HEIGHT_KEY,
  INSPECTOR_FOLDED_KEY,
  SHOTS_FOLDED_KEY,
  resetBreakdownPrefsForTests,
} from "@/lib/breakdownPrefs";

import { Breakdown } from "@/pages/Breakdown";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      getStageCoach: vi.fn(),
      getMatchCoachDistributions: vi.fn().mockResolvedValue(null),
      getStagePeaks: vi.fn().mockResolvedValue({
        duration: 20,
        sample_rate: 8000,
        bins: 4,
        peaks: [0.1, 0.5, 0.2, 0.1],
        beep_time: null,
        trimmed: true,
      }),
      patchStageShotCoach: vi.fn(),
      putStageEvents: vi.fn(),
      getScrubSettings: vi.fn().mockResolvedValue({ full_res_scrub: false }),
      setScrubSettings: vi.fn().mockResolvedValue({ full_res_scrub: true }),
      videoStreamUrl: (_slug: string, path: string, kind = "auto") => `http://localhost/${kind}/${path}`,
    },
  };
});

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => ({ mode: "local", resolved: true }) };
});

import { api } from "@/lib/api";

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Element.prototype.scrollIntoView = () => {};
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

function shot(n: number): CoachShot {
  return {
    id: `c${n}`,
    shot_number: n,
    ms_after_beep: n * 1000,
    time_from_beep: n,
    time_absolute: 5 + n,
    split: 0.3,
    interval_class: "split",
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  };
}

function coach(events: StageEvent[], version = 4): CoachStageResponse {
  return {
    stage_number: 2,
    stage_name: "Stage Two",
    beep_time: 5,
    version,
    videos: [{ path: "trimmed/stage2.mp4", role: "primary", beep_in_clip: 5, kind: "trim" }],
    shots: [shot(1), shot(2), shot(3)],
    events,
    _version: "aaaaaaaaaaaaaaaa",
  };
}

const PROJECT = {
  name: "M",
  competitor_name: "Anna",
  origin: "local",
  capabilities: ["edit", "review"],
  stages: [
    { stage_number: 1, stage_name: "Stage One", time_seconds: 0, skipped: false },
    { stage_number: 2, stage_name: "Stage Two", time_seconds: 16.2, skipped: false },
    { stage_number: 3, stage_name: "Stage Three", time_seconds: 20, skipped: false },
  ],
} as never;

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/match/:matchId/breakdown/:slug" element={<Breakdown />} />
        <Route path="/match/:matchId/breakdown/:slug/:stage" element={<><Breakdown /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  );
}

const EVENTS: StageEvent[] = [
  { id: "evt-1", kind: "movement", start: 1.2, end: 2.6, source: "manual" },
  { id: "evt-2", kind: "reload", start: 2.7, end: 4.1, source: "auto" },
];

describe("Breakdown", () => {
  beforeEach(() => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT);
    vi.mocked(api.getStageCoach).mockReset();
    vi.mocked(api.patchStageShotCoach).mockReset();
    vi.mocked(api.putStageEvents).mockReset();
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
    HTMLElement.prototype.releasePointerCapture = vi.fn();
  });

  it("the bare shooter route lands on the first stage with a time", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    renderAt("/match/m1/breakdown/anna");
    expect(await screen.findByTestId("where")).toHaveTextContent("/match/m1/breakdown/anna/2");
  });

  it("lays out the viewer, the inspector with the active shot, and the band with Audio and the lanes", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    const { container } = renderAt("/match/m1/breakdown/anna/2");
    expect(await screen.findByTestId("event-evt-1")).toBeInTheDocument();
    expect(screen.getByTestId("breakdown-workspace")).toBeInTheDocument();
    expect(container.querySelector("video")).not.toBeNull();
    // One confirmed region and one proposal: the chip counts the confirmed.
    expect(screen.getByText("1 region")).toBeInTheDocument();
    expect(screen.getByText("1 proposed")).toBeInTheDocument();
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("region", { name: "Shot 1" })).toBeInTheDocument();
    expect(within(inspector).getByRole("group", { name: "Interval class" })).toBeInTheDocument();
    // Notes and flags are Coach's review metadata, not Breakdown's.
    expect(screen.queryByRole("textbox", { name: "Coaching note" })).toBeNull();
    expect(within(inspector).getByRole("region", { name: "Shots" })).toBeInTheDocument();
    expect(screen.getByText("Audio")).toBeInTheDocument();
    expect(screen.getAllByText("Movement").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Review in Coach" })).toHaveAttribute("href", "/match/m1/coach/anna/2");
    expect(screen.getByRole("link", { name: "Previous stage" })).toHaveAttribute("href", "/match/m1/breakdown/anna/1");
    expect(screen.getByRole("link", { name: "Next stage" })).toHaveAttribute("href", "/match/m1/breakdown/anna/3");
  });

  it("a class click writes the active shot's interval as a manual override with the coach version", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    vi.mocked(api.patchStageShotCoach).mockResolvedValue(coach(EVENTS, 5));
    renderAt("/match/m1/breakdown/anna/2");
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(within(within(inspector).getByRole("group", { name: "Interval class" })).getByRole("button", { name: "Transition" }));
    await waitFor(() =>
      expect(api.patchStageShotCoach).toHaveBeenCalledWith(
        "anna",
        2,
        expect.objectContaining({ id: "c1" }),
        { interval_class: "transition", interval_class_source: "manual" },
        4,
      ),
    );
  });

  it("selecting a region puts its card in the inspector; Keep saves it through the region hook", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    vi.mocked(api.putStageEvents).mockResolvedValue({
      ...coach([EVENTS[0], { ...EVENTS[1], source: "manual" }]),
      _version: "bbbbbbbbbbbbbbbb",
    });
    renderAt("/match/m1/breakdown/anna/2");
    fireEvent.click(await screen.findByTestId("event-evt-2"));
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("region", { name: "Region" })).toBeInTheDocument();
    expect(within(inspector).queryByRole("region", { name: "Shot 1" })).toBeNull();
    fireEvent.click(within(inspector).getByRole("button", { name: "Keep" }));
    await waitFor(() =>
      expect(api.putStageEvents).toHaveBeenCalledWith(
        "anna",
        2,
        [EVENTS[0], { ...EVENTS[1], source: "manual" }],
        "aaaaaaaaaaaaaaaa",
      ),
    );
    await waitFor(() => expect(screen.getByText("2 regions")).toBeInTheDocument());
  });
});

describe("Breakdown on a short window", () => {
  it("goes dense: the transport joins the band's header, the hints wait behind Lane keys", async () => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT);
    vi.mocked(api.getStageCoach).mockReset();
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    const saved = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes("max-height"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    try {
      renderAt("/match/m1/breakdown/anna/2");
      const band = await screen.findByTestId("timeline");
      expect(screen.getByTestId("breakdown-workspace")).toHaveAttribute("data-compact", "true");
      expect(within(band).getByRole("button", { name: "Play" })).toBeInTheDocument();
      expect(screen.queryByText("Drag empty lane to add")).toBeNull();
      fireEvent.click(within(band).getByRole("button", { name: "Timeline options" }));
      fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /Lane keys/ }));
      expect(screen.getByText("Drag empty lane to add")).toBeInTheDocument();
    } finally {
      window.matchMedia = saved;
    }
  });
});

describe("Breakdown inspector scrolling", () => {
  beforeEach(() => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT);
    vi.mocked(api.getStageCoach).mockReset();
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
  });

  it("picking a shot scrolls the shot list alone, never the inspector or the page", async () => {
    const spy = vi.fn();
    const saved = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = spy;
    try {
      const { container } = renderAt("/match/m1/breakdown/anna/2");
      await screen.findByRole("complementary", { name: "Inspector" });
      fireEvent.click(container.querySelector<HTMLElement>('[aria-label="Inspector"] [data-shot-number="3"]')!);
      await waitFor(() => expect(screen.getByRole("region", { name: "Shot 3" })).toBeInTheDocument());
      expect(spy).not.toHaveBeenCalled();
    } finally {
      Element.prototype.scrollIntoView = saved;
    }
  });

  it("a new selection opens the inspector at its top: region after shot, and shot after region", async () => {
    const { container } = renderAt("/match/m1/breakdown/anna/2");
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    inspector.scrollTop = 80;
    fireEvent.click(screen.getByTestId("event-evt-2"));
    await waitFor(() => expect(within(inspector).getByRole("region", { name: "Region" })).toBeInTheDocument());
    expect(inspector.scrollTop).toBe(0);
    inspector.scrollTop = 80;
    fireEvent.click(container.querySelector<HTMLElement>('[aria-label="Inspector"] [data-shot-number="2"]')!);
    await waitFor(() => expect(within(inspector).getByRole("region", { name: "Shot 2" })).toBeInTheDocument());
    expect(inspector.scrollTop).toBe(0);
  });
});

describe("Breakdown inspector (#1372)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetBreakdownPrefsForTests();
    vi.mocked(api.getProject).mockResolvedValue(PROJECT);
    vi.mocked(api.getStageCoach).mockReset();
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    vi.mocked(api.putStageEvents).mockReset();
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
    HTMLElement.prototype.releasePointerCapture = vi.fn();
  });

  const shotsToggle = (inspector: HTMLElement) =>
    within(within(inspector).getByRole("region", { name: "Shots" })).getByRole("button", { name: /Shots/ });

  it("a region swaps the shot card for the region card and folds the shot into a line that opens it again", async () => {
    renderAt("/match/m1/breakdown/anna/2");
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("region", { name: "Shot 1" })).toBeInTheDocument();
    expect(within(inspector).queryByRole("button", { name: "Open shot 01" })).toBeNull();
    fireEvent.click(screen.getByTestId("event-evt-1"));
    expect(within(inspector).getByRole("region", { name: "Region" })).toBeInTheDocument();
    expect(within(inspector).queryByRole("region", { name: "Shot 1" })).toBeNull();
    // The list stays under the card.
    expect(within(inspector).getByRole("region", { name: "Shots" })).toBeInTheDocument();
    fireEvent.click(within(inspector).getByRole("button", { name: "Open shot 01" }));
    expect(within(inspector).getByRole("region", { name: "Shot 1" })).toBeInTheDocument();
    expect(within(inspector).queryByRole("region", { name: "Region" })).toBeNull();
  });

  it("Escape drops a selected region back to the shot view", async () => {
    renderAt("/match/m1/breakdown/anna/2");
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByTestId("event-evt-2"));
    expect(within(inspector).getByRole("region", { name: "Region" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(within(inspector).getByRole("region", { name: "Shot 1" })).toBeInTheDocument());
    expect(within(inspector).queryByRole("region", { name: "Region" })).toBeNull();
  });

  it("Escape on an open band menu closes the menu and keeps the region selected", async () => {
    renderAt("/match/m1/breakdown/anna/2");
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByTestId("event-evt-2"));
    fireEvent.click(screen.getByRole("button", { name: "Timeline options" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    // A real key press runs React's commit of the menu's close in the
    // microtask checkpoint after the menu's document listener, before any
    // window listener sees the press; jsdom runs no checkpoint mid-dispatch,
    // so a later document listener flushes it the same way.
    const flush = () => flushSync(() => {});
    document.addEventListener("keydown", flush);
    try {
      act(() => {
        document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
      });
    } finally {
      document.removeEventListener("keydown", flush);
    }
    expect(screen.queryByRole("menu")).toBeNull();
    await new Promise((r) => setTimeout(r, 20));
    expect(within(inspector).getByRole("region", { name: "Region" })).toBeInTheDocument();
  });

  it("Escape in a text field is the field's own", async () => {
    renderAt("/match/m1/breakdown/anna/2");
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByTestId("event-evt-2"));
    const field = document.createElement("input");
    document.body.appendChild(field);
    try {
      fireEvent.keyDown(field, { key: "Escape" });
      await new Promise((r) => setTimeout(r, 20));
      expect(within(inspector).getByRole("region", { name: "Region" })).toBeInTheDocument();
    } finally {
      field.remove();
    }
  });

  it("Escape during a live lane drag cancels the drag and keeps the region selected", async () => {
    const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 1000,
      height: 32,
      left: 0,
      top: 0,
      right: 1000,
      bottom: 32,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    try {
      renderAt("/match/m1/breakdown/anna/2");
      const inspector = await screen.findByRole("complementary", { name: "Inspector" });
      fireEvent.click(screen.getByTestId("event-evt-2"));
      const handle = screen.getByTestId("handle-evt-2-end");
      fireEvent.pointerDown(handle, { pointerId: 6, clientX: 200, clientY: 10, button: 0 });
      fireEvent.pointerMove(handle, { pointerId: 6, clientX: 260, clientY: 10, altKey: true });
      fireEvent.keyDown(window, { key: "Escape" });
      await new Promise((r) => setTimeout(r, 20));
      expect(within(inspector).getByRole("region", { name: "Region" })).toBeInTheDocument();
      expect(screen.getByTestId("event-evt-2")).toHaveAttribute("data-end", "4.1");
      expect(api.putStageEvents).not.toHaveBeenCalled();
    } finally {
      rect.mockRestore();
    }
  });

  it("the shot list folds to its header, remembered per browser and shared by every stage", async () => {
    renderAt("/match/m1/breakdown/anna/2");
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    const shots = within(inspector).getByRole("region", { name: "Shots" });
    expect(shotsToggle(inspector)).toHaveAttribute("aria-expanded", "true");
    expect(shots.querySelector("[data-shot-number]")).not.toBeNull();
    fireEvent.click(shotsToggle(inspector));
    expect(shotsToggle(inspector)).toHaveAttribute("aria-expanded", "false");
    expect(shots.querySelector("[data-shot-number]")).toBeNull();
    expect(window.localStorage.getItem(SHOTS_FOLDED_KEY)).toBe("on");
    // Another stage: still folded.
    fireEvent.click(screen.getByRole("link", { name: "Next stage" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/match/m1/breakdown/anna/3"));
    const next = await screen.findByRole("complementary", { name: "Inspector" });
    expect(shotsToggle(next)).toHaveAttribute("aria-expanded", "false");
  });

  it("stored folds are read on a fresh load", async () => {
    window.localStorage.setItem(SHOTS_FOLDED_KEY, "on");
    window.localStorage.setItem(INSPECTOR_FOLDED_KEY, "on");
    renderAt("/match/m1/breakdown/anna/2");
    const rail = await screen.findByRole("complementary", { name: "Inspector" });
    expect(rail).toHaveAttribute("data-folded", "true");
    fireEvent.click(within(rail).getByRole("button", { name: "Unfold inspector" }));
    expect(shotsToggle(screen.getByRole("complementary", { name: "Inspector" }))).toHaveAttribute("aria-expanded", "false");
  });

  it("the inspector folds to a rail naming the selection; the band and the video stay", async () => {
    const { container } = renderAt("/match/m1/breakdown/anna/2");
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByTestId("event-evt-2"));
    fireEvent.click(within(inspector).getByRole("button", { name: "Fold inspector" }));
    const rail = screen.getByRole("complementary", { name: "Inspector" });
    expect(rail).toHaveAttribute("data-folded", "true");
    expect(within(rail).getByTestId("inspector-rail-selection")).toHaveTextContent("Reload");
    expect(within(rail).queryByRole("region", { name: "Region" })).toBeNull();
    expect(window.localStorage.getItem(INSPECTOR_FOLDED_KEY)).toBe("on");
    // The band and the viewer are still there, and the selection still moves.
    expect(screen.getByTestId("timeline")).toBeInTheDocument();
    expect(container.querySelector("video")).not.toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(within(rail).getByTestId("inspector-rail-selection")).toHaveTextContent("Shot 01"));
    fireEvent.click(within(rail).getByRole("button", { name: "Unfold inspector" }));
    expect(screen.getByRole("complementary", { name: "Inspector" })).not.toHaveAttribute("data-folded");
    expect(window.localStorage.getItem(INSPECTOR_FOLDED_KEY)).toBe("off");
  });
});

describe("Breakdown splitter (#1373)", () => {
  // A measured page: 700 px shared by the viewer row and the band (plus the
  // 6 px splitter), a 53 px transport under a 347 px video, a band 303 px
  // tall with every row at its own height. Floor: 303 less the Reload and
  // Activation lanes (72) = 231; the video's 200 px floor caps the band at
  // 700 - 53 - 200 = 447.
  const box = (top: number, height: number) =>
    ({ top, bottom: top + height, height, left: 0, right: 1000, width: 1000, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
  let rect: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    window.localStorage.clear();
    resetBreakdownPrefsForTests();
    vi.mocked(api.getProject).mockResolvedValue(PROJECT);
    vi.mocked(api.getStageCoach).mockReset();
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    rect = vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      const el = this as HTMLElement;
      if (el.dataset.testid === "breakdown-room") return box(0, 706);
      if (el.tagName === "VIDEO") return box(0, 347);
      if (el.firstElementChild?.tagName === "VIDEO") return box(0, 400);
      if (el.parentElement?.dataset.testid === "breakdown-band") return box(0, 303);
      return box(0, 0);
    });
  });
  afterEach(() => rect.mockRestore());

  const band = () => screen.getByTestId("breakdown-band");
  const audioRow = () => screen.getByText("Audio").parentElement!;

  it("with nothing remembered the band keeps its own height and the Audio row its own", async () => {
    renderAt("/match/m1/breakdown/anna/2");
    const sep = await screen.findByRole("separator", { name: "Resize the timeline" });
    expect(band().style.height).toBe("");
    expect(audioRow().style.height).toBe("56px");
    expect(sep).toHaveAttribute("aria-valuemin", String(700 - 447));
    expect(sep).toHaveAttribute("aria-valuemax", String(700 - 231));
  });

  it("a remembered split is drawn, clamped to this window, and its extra height goes to the Audio row", async () => {
    window.localStorage.setItem(BAND_HEIGHT_KEY, "350");
    const { unmount } = renderAt("/match/m1/breakdown/anna/2");
    await screen.findByRole("separator");
    expect(band().style.height).toBe("350px");
    expect(audioRow().style.height).toBe(`${56 + 350 - 303}px`);
    unmount();
    window.localStorage.setItem(BAND_HEIGHT_KEY, "9999");
    resetBreakdownPrefsForTests();
    renderAt("/match/m1/breakdown/anna/2");
    await screen.findByRole("separator");
    expect(band().style.height).toBe("447px");
  });

  it("keys move the split within its limits and remember it; double-click toggles the band-large preset", async () => {
    renderAt("/match/m1/breakdown/anna/2");
    const sep = await screen.findByRole("separator");
    fireEvent.keyDown(sep, { key: "End" });
    expect(band().style.height).toBe("231px");
    expect(window.localStorage.getItem(BAND_HEIGHT_KEY)).toBe("231");
    fireEvent.keyDown(sep, { key: "ArrowUp" });
    expect(window.localStorage.getItem(BAND_HEIGHT_KEY)).toBe("247");
    fireEvent.doubleClick(sep);
    expect(band().style.height).toBe("447px");
    expect(window.localStorage.getItem(BAND_HEIGHT_KEY)).toBe("447");
    fireEvent.doubleClick(sep);
    expect(band().style.height).toBe("247px");
    expect(window.localStorage.getItem(BAND_HEIGHT_KEY)).toBe("247");
  });

  it("a band under its natural height scrolls only its rows: the header and the ruler stay", async () => {
    window.localStorage.setItem(BAND_HEIGHT_KEY, "231");
    renderAt("/match/m1/breakdown/anna/2");
    await screen.findByRole("separator");
    const rows = screen.getByTestId("timeline-rows");
    const gutter = screen.getByTestId("timeline-gutter-rows");
    // Audio, Shots and one lane: 303 - 231 = 72 px of rows below.
    expect(rows.style.height).toBe("124px");
    expect(gutter.style.height).toBe(rows.style.height);
    expect(rows).toHaveClass("overflow-y-auto");
    expect(band()).toHaveClass("overflow-hidden");
    // The ruler and the band's header are outside the scroller.
    expect(rows.contains(screen.getByTestId("timeline-ruler"))).toBe(false);
    expect(rows.contains(screen.getByRole("button", { name: "Timeline options" }))).toBe(false);
    expect(within(rows).getByTestId("event-evt-2")).toBeInTheDocument();
    expect(within(gutter).getByText("Activation")).toBeInTheDocument();
    // The two scrollers move together.
    rows.scrollTop = 40;
    fireEvent.scroll(rows);
    expect(gutter.scrollTop).toBe(40);
    gutter.scrollTop = 10;
    fireEvent.scroll(gutter);
    expect(rows.scrollTop).toBe(10);
  });

  it("at or above its natural height the band's rows are not boxed", async () => {
    window.localStorage.setItem(BAND_HEIGHT_KEY, "350");
    renderAt("/match/m1/breakdown/anna/2");
    await screen.findByRole("separator");
    expect(screen.queryByTestId("timeline-rows")).toBeNull();
    expect(screen.queryByTestId("timeline-gutter-rows")).toBeNull();
  });

  it("a drag on the handle remembers the split it lands on", async () => {
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    window.localStorage.setItem(BAND_HEIGHT_KEY, "300");
    renderAt("/match/m1/breakdown/anna/2");
    const sep = await screen.findByRole("separator");
    fireEvent.pointerDown(sep, { pointerId: 1, clientY: 400, button: 0 });
    fireEvent.pointerMove(sep, { pointerId: 1, clientY: 350 });
    expect(band().style.height).toBe("350px");
    expect(window.localStorage.getItem(BAND_HEIGHT_KEY)).toBe("300");
    fireEvent.pointerUp(sep, { pointerId: 1, clientY: 350 });
    expect(window.localStorage.getItem(BAND_HEIGHT_KEY)).toBe("350");
  });
});
