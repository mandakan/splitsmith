/**
 * Breakdown on a phone (spec 2026-10-17): the proposal queue, Keep /
 * Delete / Kind, edge mode (one save on Done, none on Cancel or
 * pointercancel), a region that vanishes on reload, and a read-only mirror.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type CoachShot, type CoachStageResponse, type StageEvent } from "@/lib/api";
import { COMMIT_DEBOUNCE_MS } from "@/lib/useStageEvents";

import { MobileBreakdown } from "@/pages/MobileBreakdown";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      getStageCoach: vi.fn(),
      getMatchCoachDistributions: vi.fn().mockResolvedValue(null),
      getStagePeaks: vi.fn().mockResolvedValue(null),
      patchStageShotCoach: vi.fn(),
      putStageEvents: vi.fn(),
      reclassifyStageCoach: vi.fn(),
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
  // A phone: every media query that asks for a narrow viewport matches.
  window.matchMedia = ((query: string) => ({
    matches: query.includes("max-width"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

function makeShot(n: number): CoachShot {
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

function stageWith(events: StageEvent[], version = "aaaaaaaaaaaaaaaa"): CoachStageResponse {
  return {
    stage_number: 1,
    stage_name: "Stage One",
    beep_time: 5,
    version: 4,
    videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5, kind: "trim" }],
    shots: [makeShot(1)],
    events,
    _version: version,
  };
}

const PROJECT = {
  name: "M",
  competitor_name: "Anna",
  stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }],
} as never;

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/match/m1/breakdown/anna/1"]}>
      <Routes>
        <Route path="/match/:matchId/breakdown/:slug/:stage" element={<MobileBreakdown />} />
      </Routes>
    </MemoryRouter>,
  );
}

function padOf(name = "edge-pad") {
  const el = screen.getAllByTestId(name)[0];
  Object.defineProperty(el, "clientWidth", { value: 300, configurable: true });
  return el;
}

const settle = () => new Promise((r) => setTimeout(r, COMMIT_DEBOUNCE_MS + 80));

const R1: StageEvent = { id: "r1", kind: "reload", start: 4, end: 5.2, source: "auto" };
const M1: StageEvent = { id: "m1", kind: "movement", start: 2, end: 3, source: "auto" };
const K: StageEvent = { id: "k", kind: "reload", start: 8, end: 9, source: "manual" };

beforeEach(() => {
  vi.mocked(api.getProject).mockReset();
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
  vi.mocked(api.getStageCoach).mockReset();
  vi.mocked(api.putStageEvents).mockReset();
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});

describe("MobileBreakdown", () => {
  it("asks for proposals and opens on the first one by start", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1, M1, K]));
    renderPage();
    expect(await screen.findByText("Proposal 1 of 2")).toBeTruthy();
    expect(screen.getByTestId("open-region").textContent).toContain("Movement");
    expect(api.getStageCoach).toHaveBeenCalledWith("anna", 1, { seed: true });
  });

  it("Keep confirms the open proposal, saves once and moves on", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1, M1], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockResolvedValue(stageWith([R1, { ...M1, source: "manual" }], "v2v2v2v2v2v2v2v2"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep" }));
    await waitFor(() =>
      expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [R1, { ...M1, source: "manual" }], "v1v1v1v1v1v1v1v1"),
    );
    expect(await screen.findByText("Proposal 1 of 1")).toBeTruthy();
    expect(screen.getByTestId("open-region").textContent).toContain("Reload");
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
  });

  it("Delete removes it and moves on", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1, M1], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockResolvedValue(stageWith([R1], "v2v2v2v2v2v2v2v2"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [R1], "v1v1v1v1v1v1v1v1"));
    expect(screen.getByTestId("open-region").textContent).toContain("Reload");
  });

  it("disables a kind that would overlap, saying why", async () => {
    const otherReload: StageEvent = { id: "r0", kind: "reload", start: 2.2, end: 2.8, source: "manual" };
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([M1, otherReload]));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Kind" }));
    const reload = await screen.findByRole("button", { name: "Reload" });
    expect(reload).toHaveProperty("disabled", true);
    expect(reload.getAttribute("title")).toContain("overlap");
    expect(screen.getByRole("button", { name: "Activation" })).toHaveProperty("disabled", false);
  });

  it("edge mode saves nothing until Done, then once", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events) => stageWith(events, "v2v2v2v2v2v2v2v2"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Adjust start/ }));
    const pad = padOf();
    fireEvent.pointerDown(pad, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(pad, { pointerId: 1, clientX: 130 });
    fireEvent.pointerUp(pad, { pointerId: 1, clientX: 130 });
    await settle();
    expect(api.putStageEvents).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(1));
    const sent = vi.mocked(api.putStageEvents).mock.calls[0][2][0];
    expect(sent.start).toBeCloseTo(4.1);
    expect(sent.source).toBe("manual");
    // The only proposal is now confirmed: the queue is empty.
    expect(await screen.findByText("No proposals on this stage")).toBeTruthy();
  });

  it("Cancel restores the region edge mode found and saves nothing", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1]));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Adjust end/ }));
    const pad2 = padOf();
    fireEvent.pointerDown(pad2, { pointerId: 2, clientX: 100 });
    fireEvent.pointerMove(pad2, { pointerId: 2, clientX: 160 });
    fireEvent.pointerUp(pad2, { pointerId: 2, clientX: 160 });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await settle();
    expect(api.putStageEvents).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Adjust end, 5.20 s" })).toBeTruthy();
    expect(screen.getByText("Proposal 1 of 1")).toBeTruthy();
  });

  it("dragging a handle on the strip moves that edge under the finger, saved on Done", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1]));
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events) => stageWith(events));
    renderPage();
    const handle = await screen.findByRole("button", { name: /Adjust start/ });
    // R1 is 4-5.2 s in a 16.2 s stage: its window is 2-7.2 s, 5.2 s wide.
    Object.defineProperty(screen.getByTestId("focus-strip"), "clientWidth", { value: 300, configurable: true });
    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 115 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 130 });
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 130 });
    expect(screen.getByRole("button", { name: "Done" })).toBeTruthy();
    await settle();
    expect(api.putStageEvents).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.putStageEvents).mock.calls[0][2][0].start).toBeCloseTo(4.52);
  });

  it("a second gesture in one edge session continues from where the first left the edge", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1]));
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events) => stageWith(events));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Adjust start/ }));
    const pad = padOf();
    fireEvent.pointerDown(pad, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(pad, { pointerId: 1, clientX: 130 });
    fireEvent.pointerUp(pad, { pointerId: 1, clientX: 130 });
    fireEvent.pointerDown(pad, { pointerId: 2, clientX: 200 });
    fireEvent.pointerMove(pad, { pointerId: 2, clientX: 230 });
    fireEvent.pointerUp(pad, { pointerId: 2, clientX: 230 });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.putStageEvents).mock.calls[0][2][0].start).toBeCloseTo(4.2);
  });

  it("Done with nothing changed saves nothing and leaves the proposal a proposal", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1]));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Adjust start/ }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await settle();
    expect(api.putStageEvents).not.toHaveBeenCalled();
    expect(screen.getByText("Proposal 1 of 1")).toBeTruthy();
  });

  it("Done after an edit confirms the proposal and moves on to the next one", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([M1, R1]));
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events) => stageWith(events));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Adjust start/ }));
    fireEvent.click(screen.getByRole("button", { name: "+1 frame" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.getByTestId("open-region").textContent).toContain("Reload"));
    expect(screen.getByText("Proposal 1 of 1")).toBeTruthy();
  });

  it("a new kind confirms the proposal and moves on; the current kind changes nothing", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([M1, R1]));
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events) => stageWith(events));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Kind" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Movement/ }));
    await settle();
    expect(api.putStageEvents).not.toHaveBeenCalled();
    expect(screen.getByTestId("open-region").textContent).toContain("Movement");

    fireEvent.click(screen.getByRole("button", { name: "Kind" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Activation/ }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.putStageEvents).mock.calls[0][2].find((e) => e.id === "m1")?.kind).toBe("activation");
    expect(screen.getByTestId("open-region").textContent).toContain("Reload");
  });

  it("playing turns Play region into Pause, which stops it, and edge mode pauses", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1]));
    renderPage();
    await screen.findByRole("button", { name: "Play region" });
    const video = screen.getByTestId("phone-video").querySelector("video")!;
    const pause = vi.fn();
    Object.defineProperty(video, "pause", { value: pause, configurable: true });
    fireEvent.play(video);
    fireEvent.click(await screen.findByRole("button", { name: "Pause" }));
    expect(pause).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /Adjust start/ }));
    expect(pause).toHaveBeenCalledTimes(2);
  });

  it("a frame step moves the edge by one frame of the clip", async () => {
    const at50 = { ...stageWith([R1]), videos: [{ ...stageWith([]).videos[0], fps: 50 }] };
    vi.mocked(api.getStageCoach).mockResolvedValue(at50);
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events) => stageWith(events));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Adjust start/ }));
    fireEvent.click(screen.getByRole("button", { name: "+1 frame" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.putStageEvents).mock.calls[0][2][0].start).toBeCloseTo(4.02);
  });

  it("a pointercancel in edge mode restores the region and saves nothing", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1]));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Adjust start/ }));
    const pad = padOf();
    fireEvent.pointerDown(pad, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(pad, { pointerId: 1, clientX: 200 });
    fireEvent.pointerCancel(pad, { pointerId: 1 });
    expect(await screen.findByRole("button", { name: "Adjust start, 4.00 s" })).toBeTruthy();
    await settle();
    expect(api.putStageEvents).not.toHaveBeenCalled();
  });

  it("a region that vanishes on reload opens the next one", async () => {
    vi.mocked(api.getStageCoach)
      .mockResolvedValueOnce(stageWith([M1, R1], "v1v1v1v1v1v1v1v1"))
      .mockResolvedValueOnce(stageWith([M1], "v3v3v3v3v3v3v3v3"));
    vi.mocked(api.putStageEvents).mockRejectedValue(new ApiError(409, "version_conflict", { code: "version_conflict" }));
    renderPage();
    // Keep the movement: the page moves on to the reload, then the 409's
    // reload brings back a list where another writer removed that reload.
    fireEvent.click(await screen.findByRole("button", { name: "Keep" }));
    await waitFor(() => expect(api.getStageCoach).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId("open-region").textContent).toContain("Movement"));
  });

  it("says when the stage has no proposals", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([K]));
    renderPage();
    expect(await screen.findByText("No proposals on this stage")).toBeTruthy();
  });

  it("a mirror without the edit capability reads only", async () => {
    vi.mocked(api.getProject).mockResolvedValue({
      ...(PROJECT as object),
      origin: "desktop",
      capabilities: ["review"],
    } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1]));
    renderPage();
    expect(await screen.findByText(/Edit regions on the desktop/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Keep" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Adjust/ })).toBeNull();
  });
});
