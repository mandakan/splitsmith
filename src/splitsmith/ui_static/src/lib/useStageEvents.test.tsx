/**
 * useStageEvents: the Coach page's region state and its writes. Every
 * PUT appends an audit event server-side, so drag frames never write, a
 * burst of commits is one PUT, and a commit waits for the one in flight
 * so it carries the revision that one returned.
 */
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LaneEditor } from "@/components/coach/LaneEditor";

import { ApiError, api, type CoachStageResponse, type StageEvent } from "./api";
import { COMMIT_DEBOUNCE_MS, useStageEvents } from "./useStageEvents";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, api: { ...actual.api, putStageEvents: vi.fn(), getStageCoach: vi.fn() } };
});

const ev = (id: string, start: number, end: number): StageEvent => ({ id, kind: "movement", start, end, source: "manual" });

function coach(events: StageEvent[], version: string): CoachStageResponse {
  return { stage_number: 1, stage_name: "S", beep_time: 0, version: 1, videos: [], shots: [], events, _version: version };
}

function setup() {
  const applyCoach = vi.fn();
  const onError = vi.fn();
  const hook = renderHook(() => useStageEvents("anna", 1, applyCoach, onError));
  act(() => hook.result.current.apply(coach([ev("evt-1", 1, 2)], "v1")));
  return { ...hook, applyCoach, onError };
}

const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(COMMIT_DEBOUNCE_MS); });

describe("useStageEvents", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api.putStageEvents).mockReset();
    vi.mocked(api.getStageCoach).mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("adopts a response's events and revision, and drops a selection that is gone", () => {
    const { result, applyCoach } = setup();
    expect(applyCoach).toHaveBeenCalledTimes(1);
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1"]);
    act(() => result.current.select("evt-1"));
    act(() => result.current.apply(coach([ev("evt-2", 3, 4)], "v2")));
    expect(result.current.selectedId).toBeNull();
  });

  it("drag frames update local state and never write", async () => {
    const { result } = setup();
    act(() => result.current.change([ev("evt-1", 1.1, 2)], false));
    act(() => result.current.change([ev("evt-1", 1.2, 2)], false));
    await settle();
    await settle();
    expect(result.current.events[0].start).toBe(1.2);
    expect(api.putStageEvents).not.toHaveBeenCalled();
  });

  it("a burst of commits inside the debounce is one PUT with the last list", async () => {
    const { result } = setup();
    vi.mocked(api.putStageEvents).mockResolvedValue(coach([ev("evt-1", 1.3, 2)], "v2"));
    const almost = () => act(async () => { await vi.advanceTimersByTimeAsync(COMMIT_DEBOUNCE_MS - 1); });
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await almost();
    act(() => result.current.change([ev("evt-1", 1.2, 2)], true));
    await almost();
    act(() => result.current.change([ev("evt-1", 1.3, 2)], true));
    await almost();
    // Each commit restarts the wait; nothing has gone out yet.
    expect(api.putStageEvents).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [ev("evt-1", 1.3, 2)], "v1");
  });

  it("a commit waits for the PUT in flight and sends the revision it returned", async () => {
    const { result } = setup();
    let finishFirst: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((r) => { finishFirst = r; }))
      .mockResolvedValueOnce(coach([ev("evt-1", 1.4, 2)], "v3"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    act(() => result.current.change([ev("evt-1", 1.4, 2)], true));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    await act(async () => { finishFirst(coach([ev("evt-1", 1.1, 2)], "v2")); });
    // The first response is superseded: local state keeps the newer edit.
    expect(result.current.events[0].start).toBe(1.4);
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.putStageEvents).mock.calls[1][3]).toBe("v2");
  });

  it("a PUT resolving while a newer edit waits in the debounce keeps the edit and takes only the revision", async () => {
    const { result } = setup();
    let finishFirst: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((r) => { finishFirst = r; }))
      .mockResolvedValueOnce(coach([ev("evt-1", 1.4, 2)], "v3"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // A goes out
    act(() => result.current.change([ev("evt-1", 1.4, 2)], true)); // B waits in the debounce
    await act(async () => { finishFirst(coach([ev("evt-1", 1.1, 2)], "v2")); });
    expect(result.current.events[0].start).toBe(1.4);
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.putStageEvents).mock.calls[1][3]).toBe("v2");
  });

  it("a 409 also cancels an edit still waiting in the debounce", async () => {
    const { result } = setup();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }));
    vi.mocked(api.getStageCoach).mockResolvedValue(coach([ev("evt-9", 5, 6)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // A goes out
    act(() => result.current.change([ev("evt-1", 1.4, 2)], true)); // B waits in the debounce
    await act(async () => { failFirst(new ApiError(409, "version_conflict", { code: "version_conflict" })); });
    await settle();
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-9"]);
  });

  it("a 409 reloads the payload and drops a commit queued behind it", async () => {
    const { result, onError } = setup();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }));
    vi.mocked(api.getStageCoach).mockResolvedValue(coach([ev("evt-9", 5, 6)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    act(() => result.current.change([ev("evt-1", 1.4, 2)], true));
    await settle();
    await act(async () => { failFirst(new ApiError(409, "version_conflict", { code: "version_conflict" })); });
    expect(api.getStageCoach).toHaveBeenCalledWith("anna", 1);
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-9"]);
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it("any other failure is reported", async () => {
    const { result, onError } = setup();
    vi.mocked(api.putStageEvents).mockRejectedValue(new ApiError(500, "boom"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    expect(onError).toHaveBeenCalledWith("boom");
  });

  it("a PUT resolving during a drag keeps the dragged list and takes only the revision", async () => {
    const { result } = setup();
    let finishFirst: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((r) => { finishFirst = r; }))
      .mockResolvedValueOnce(coach([], "v3"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // A goes out
    const drawing = [ev("evt-1", 1.1, 2), ev("evt-2", 3, 3.5)];
    act(() => result.current.change(drawing, false)); // a create's live frame
    await act(async () => { finishFirst(coach([ev("evt-1", 1.1, 2)], "v2")); });
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-2"]);
    act(() => result.current.change(drawing, true)); // release
    await settle();
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, drawing, "v2");
  });

  it("a commit whose lanes overlap reverts to the last valid list and never goes out", async () => {
    const { result, onError } = setup();
    act(() => result.current.change([ev("evt-1", 1, 2), ev("evt-2", 1.5, 3)], true));
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1"]);
    await settle();
    expect(api.putStageEvents).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    // A later valid commit goes out with its own list, not the overlap.
    vi.mocked(api.putStageEvents).mockResolvedValue(coach([ev("evt-1", 1, 2.5)], "v2"));
    act(() => result.current.change([ev("evt-1", 1, 2.5)], true));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [ev("evt-1", 1, 2.5)], "v1");
  });

  it("unmounting flushes a pending commit instead of dropping it", async () => {
    const { result, unmount } = setup();
    vi.mocked(api.putStageEvents).mockResolvedValue(coach([], "v2"));
    act(() => result.current.change([], true));
    unmount();
    await act(async () => {});
    expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [], "v1");
  });
});

describe("useStageEvents: an in-flight save never reverts or drops an edit (#1322)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api.putStageEvents).mockReset();
    vi.mocked(api.getStageCoach).mockReset();
  });
  afterEach(() => vi.useRealTimers());

  const conflict = () => new ApiError(409, "version_conflict", { code: "version_conflict" });
  const summary = { movement_s: 0, moving_shots: 3, reloads: 0, reload_avg_s: null, overhang_s: 0, capacity_warning: null };

  function setupWithDiscard() {
    const applyCoach = vi.fn();
    const onError = vi.fn();
    const onDiscard = vi.fn();
    const hook = renderHook(() => useStageEvents("anna", 1, applyCoach, onError, onDiscard));
    act(() => hook.result.current.apply(coach([ev("evt-1", 1, 2)], "v1")));
    return { ...hook, applyCoach, onError, onDiscard };
  }

  // Sequence 1: Esc / pointercancel emits the restored list with commit=false.
  it("a response landing after a cancelled drag is applied in full", async () => {
    const { result, applyCoach } = setupWithDiscard();
    let finishFirst: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((r) => { finishFirst = r; }));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // A goes out
    act(() => result.current.change([ev("evt-1", 1.5, 2)], false)); // a drag frame
    act(() => result.current.change([ev("evt-1", 1.1, 2)], false)); // Esc emits the restored list
    act(() => result.current.cancel());
    const answer: CoachStageResponse = { ...coach([ev("evt-1", 1.1, 2)], "v2"), event_summary: summary };
    await act(async () => { finishFirst(answer); });
    expect(applyCoach).toHaveBeenLastCalledWith(answer);
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
  });

  it("a response landing mid-drag takes the payload but not the list, and the cancel adopts its list", async () => {
    const { result, applyCoach } = setupWithDiscard();
    let finishFirst: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((r) => { finishFirst = r; }));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    act(() => result.current.change([ev("evt-1", 1.5, 2)], false));
    const answer: CoachStageResponse = { ...coach([ev("evt-1", 1.1, 2)], "v2"), event_summary: summary };
    await act(async () => { finishFirst(answer); });
    // Mid-drag it takes the payload (shot classes, summary) but keeps the dragged list ...
    expect(applyCoach).toHaveBeenLastCalledWith(answer);
    expect(result.current.events[0].start).toBe(1.5);
    act(() => result.current.change([ev("evt-1", 1.5, 2.5)], false));
    act(() => result.current.cancel());
    // ... and the cancel adopts the server's list, not the last frame.
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
  });

  // Sequence 2: a 409 reload while a create is live.
  it("a 409 reload during a live create keeps the region under the pointer", async () => {
    const { result, onDiscard } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }));
    // Another writer changed the regions: the conflict is about them.
    vi.mocked(api.getStageCoach).mockResolvedValue(coach([ev("evt-1", 1, 2), ev("evt-5", 7, 8)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // A goes out
    const drawing = [ev("evt-1", 1.1, 2), ev("evt-2", 3, 3.5)];
    act(() => result.current.change(drawing, false));
    await act(async () => { failFirst(conflict()); });
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-2"]);
    expect(onDiscard).toHaveBeenCalledTimes(1);
    // The release was drawn on the list the conflict replaced: dropped, not sent.
    act(() => result.current.change(drawing, true));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5"]);
    // One conflict, one discard report.
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("a cancel after a 409 that changed the regions adopts the reload and reports the discard once", async () => {
    const { result, onDiscard } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }));
    vi.mocked(api.getStageCoach).mockResolvedValue(coach([ev("evt-1", 1, 2), ev("evt-5", 7, 8)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    act(() => result.current.change([ev("evt-1", 1.1, 2), ev("evt-2", 3, 3.5)], false));
    await act(async () => { failFirst(conflict()); });
    act(() => result.current.change([ev("evt-1", 1.1, 2)], false));
    act(() => result.current.cancel());
    await settle();
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5"]);
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("a 409 during a live create whose reload leaves the regions alone lets the release go out on the fresh revision", async () => {
    const { result, onDiscard } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }))
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v10"));
    vi.mocked(api.getStageCoach).mockResolvedValue(coach([ev("evt-1", 1, 2)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    const drawing = [ev("evt-1", 1.1, 2), ev("evt-2", 3, 3.5)];
    act(() => result.current.change(drawing, false));
    await act(async () => { failFirst(conflict()); });
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-2"]);
    act(() => result.current.change(drawing, true));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, drawing, "v9");
    expect(result.current.events).toEqual(drawing);
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("a cancel after a 409 that left the regions alone still saves the edit the 409 hit", async () => {
    const { result } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }))
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v10"));
    vi.mocked(api.getStageCoach).mockResolvedValue(coach([ev("evt-1", 1, 2)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    act(() => result.current.change([ev("evt-1", 1.1, 2), ev("evt-2", 3, 3.5)], false));
    await act(async () => { failFirst(conflict()); });
    act(() => result.current.change([ev("evt-1", 1.1, 2)], false));
    act(() => result.current.cancel());
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.1, 2)], "v9");
  });

  // Sequence 3: a shot PATCH moved the revision under an events PUT.
  it("a 409 whose reload leaves the regions alone re-sends the edit once on the fresh revision", async () => {
    const { result, applyCoach, onDiscard } = setupWithDiscard();
    vi.mocked(api.putStageEvents)
      .mockRejectedValueOnce(conflict())
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v10"));
    // The shot PATCH changed a shot, not the regions.
    vi.mocked(api.getStageCoach).mockResolvedValue({ ...coach([ev("evt-1", 1, 2)], "v9"), version: 2 });
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.1, 2)], "v9");
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
    expect(applyCoach).toHaveBeenLastCalledWith(coach([ev("evt-1", 1.1, 2)], "v10"));
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("the re-send carries the newest local edit, not the one the 409 hit", async () => {
    const { result } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }))
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v10"));
    vi.mocked(api.getStageCoach).mockResolvedValue(coach([ev("evt-1", 1, 2)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // A goes out
    act(() => result.current.change([ev("evt-1", 1.4, 2)], true)); // B waits in the debounce
    await act(async () => { failFirst(conflict()); });
    await settle();
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.4, 2)], "v9");
    expect(result.current.events[0].start).toBe(1.4);
  });

  it("a second 409 on the re-send discards the edit and reports it", async () => {
    const { result, onDiscard, onError } = setupWithDiscard();
    vi.mocked(api.putStageEvents).mockRejectedValue(conflict());
    vi.mocked(api.getStageCoach)
      .mockResolvedValueOnce(coach([ev("evt-1", 1, 2)], "v9"))
      .mockResolvedValueOnce(coach([ev("evt-1", 1, 2)], "v11"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(result.current.events).toEqual([ev("evt-1", 1, 2)]);
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  // Fix round 1: a foreign response (shot PATCH, reclassify) goes through ``apply``.
  it("a shot PATCH response landing between the PUT and its 409 does not replace the edit the re-send carries", async () => {
    const { result, applyCoach, onDiscard } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }))
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v10"));
    const patched = { ...coach([ev("evt-1", 1, 2)], "v9"), version: 2 };
    vi.mocked(api.getStageCoach).mockResolvedValue(patched);
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // the PUT goes out on v1
    act(() => result.current.apply(patched)); // the PATCH's response, handled first server-side
    expect(applyCoach).toHaveBeenLastCalledWith(patched);
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
    await act(async () => { failFirst(conflict()); });
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.1, 2)], "v9");
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("a shot PATCH response landing during the 409's reload does not replace the edit either", async () => {
    const { result, onDiscard } = setupWithDiscard();
    let finishReload: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockRejectedValueOnce(conflict())
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v10"));
    const patched = { ...coach([ev("evt-1", 1, 2)], "v9"), version: 2 };
    vi.mocked(api.getStageCoach).mockImplementationOnce(() => new Promise((r) => { finishReload = r; }));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // PUT 409s, the reload is in flight
    act(() => result.current.apply(patched));
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
    await act(async () => { finishReload(patched); });
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.1, 2)], "v9");
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("a response applied with nothing pending still replaces the list", () => {
    const { result } = setupWithDiscard();
    act(() => result.current.apply(coach([ev("evt-3", 4, 5)], "v2")));
    expect(result.current.events).toEqual([ev("evt-3", 4, 5)]);
  });

  it("a shot PATCH response mid-create keeps the region under the pointer and the release commits it", async () => {
    const { result, applyCoach } = setupWithDiscard();
    vi.mocked(api.putStageEvents).mockImplementationOnce(async (_s, _n, events) => coach(events, "v10"));
    const drawing = [ev("evt-1", 1, 2), ev("evt-2", 3, 3.5)];
    act(() => result.current.change(drawing, false));
    const patched = { ...coach([ev("evt-1", 1, 2)], "v9"), version: 2 };
    act(() => result.current.apply(patched));
    expect(applyCoach).toHaveBeenLastCalledWith(patched);
    expect(result.current.events).toEqual(drawing);
    act(() => result.current.change(drawing, true));
    await settle();
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, drawing, "v9");
  });

  it("a shot PATCH response landing after a 409 left a re-send owed to the drag, then Esc, sends the edit", async () => {
    const { result } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }))
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v12"));
    vi.mocked(api.getStageCoach).mockResolvedValue(coach([ev("evt-1", 1, 2)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    act(() => result.current.change([ev("evt-1", 1.1, 2), ev("evt-2", 3, 3.5)], false));
    await act(async () => { failFirst(conflict()); }); // re-send owed to the drag
    act(() => result.current.apply({ ...coach([ev("evt-1", 1, 2)], "v11"), version: 3 })); // another PATCH
    act(() => result.current.change([ev("evt-1", 1.1, 2)], false));
    act(() => result.current.cancel());
    await settle();
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.1, 2)], "v11");
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
  });

  it("a re-send that fails with a non-409 error does not use up the next conflict's re-send", async () => {
    const { result, onDiscard } = setupWithDiscard();
    vi.mocked(api.putStageEvents)
      .mockRejectedValueOnce(conflict())
      .mockRejectedValueOnce(new ApiError(500, "boom"))
      .mockRejectedValueOnce(conflict())
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v20"));
    vi.mocked(api.getStageCoach)
      .mockResolvedValueOnce(coach([ev("evt-1", 1, 2)], "v9"))
      .mockResolvedValueOnce(coach([ev("evt-1", 1, 2)], "v19"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // 409, re-send, 500
    act(() => result.current.change([ev("evt-1", 1.2, 2)], true));
    await settle(); // an unrelated 409: still gets its one re-send
    expect(api.putStageEvents).toHaveBeenCalledTimes(4);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.2, 2)], "v19");
    expect(onDiscard).not.toHaveBeenCalled();
  });

  // Fix round 2: a foreign response carrying another writer's regions.
  const withEvt5 = () => ({ ...coach([ev("evt-1", 1, 2), ev("evt-5", 7, 8)], "v6"), version: 2 });

  it("another writer's region arriving on a PATCH response before the 409 is kept: the edit is discarded, not re-sent", async () => {
    const { result, applyCoach, onDiscard } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }));
    vi.mocked(api.getStageCoach).mockResolvedValue(withEvt5());
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // the PUT goes out on v1
    act(() => result.current.apply(withEvt5())); // the PATCH response, evt-5 from another tab
    expect(applyCoach).toHaveBeenLastCalledWith(withEvt5()); // the payload is still taken
    await act(async () => { failFirst(conflict()); });
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5"]);
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("another writer's region arriving mid-drag is not overwritten by the release", async () => {
    const { result, onDiscard } = setupWithDiscard();
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events, version) => {
      if (version !== "v6") throw conflict();
      return coach(events, "v7");
    });
    vi.mocked(api.getStageCoach).mockResolvedValue(withEvt5());
    act(() => result.current.change([ev("evt-1", 1.3, 2)], false)); // an edge drag
    act(() => result.current.apply(withEvt5()));
    expect(result.current.events).toEqual([ev("evt-1", 1.3, 2)]);
    act(() => result.current.change([ev("evt-1", 1.3, 2)], true)); // release
    await settle();
    // The release went out on the revision it was drawn on, 409'd, and the server's list won.
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.putStageEvents).mock.calls[0][3]).toBe("v1");
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5"]);
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("another writer's region arriving while a commit waits in the debounce is not overwritten by it", async () => {
    const { result, onDiscard } = setupWithDiscard();
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events, version) => {
      if (version !== "v6") throw conflict();
      return coach(events, "v7");
    });
    vi.mocked(api.getStageCoach).mockResolvedValue(withEvt5());
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true)); // waits in the debounce
    act(() => result.current.apply(withEvt5()));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.putStageEvents).mock.calls[0][3]).toBe("v1");
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5"]);
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("a foreign response with the same regions while a commit waits still advances the revision", async () => {
    const { result } = setupWithDiscard();
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events) => coach(events, "v7"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    act(() => result.current.apply({ ...coach([ev("evt-1", 1, 2)], "v6"), version: 2 }));
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [ev("evt-1", 1.1, 2)], "v6");
  });

  // Fix round 3: a withheld response is applied once nothing is outstanding.
  const putOnV6 = () =>
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events, version) => {
      if (version !== "v6") throw conflict();
      return coach(events, "v7");
    });

  it("another writer's region withheld during a drag shows on Esc, and the next edit saves on its revision", async () => {
    const { result, onDiscard } = setupWithDiscard();
    putOnV6();
    act(() => result.current.change([ev("evt-1", 1.3, 2)], false)); // a drag
    act(() => result.current.apply(withEvt5())); // withheld: the drag is outstanding
    act(() => result.current.change([ev("evt-1", 1, 2)], false)); // Esc emits the restored list
    act(() => result.current.cancel());
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5"]);
    const next = [ev("evt-1", 1.5, 2), ev("evt-5", 7, 8)];
    act(() => result.current.change(next, true)); // a fresh edit with nothing outstanding
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, next, "v6");
    expect(result.current.events).toEqual(next);
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("another writer's region withheld during a drag shows after a release that overlaps, and the next edit saves", async () => {
    const { result, onDiscard } = setupWithDiscard();
    putOnV6();
    act(() => result.current.change([ev("evt-1", 1.3, 2)], false));
    act(() => result.current.apply(withEvt5()));
    act(() => result.current.change([ev("evt-1", 1, 2), ev("evt-2", 1.5, 3)], true)); // overlaps: never goes out
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5"]);
    const next = [ev("evt-1", 1.5, 2), ev("evt-5", 7, 8)];
    act(() => result.current.change(next, true));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, next, "v6");
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("another writer's region withheld while a PUT is in flight shows when that PUT fails with a non-409 error", async () => {
    const { result, onError, onDiscard } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }))
      .mockImplementation(async (_s, _n, events, version) => {
        if (version !== "v6") throw conflict();
        return coach(events, "v7");
      });
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // in flight on v1
    act(() => result.current.apply(withEvt5())); // withheld
    await act(async () => { failFirst(new ApiError(500, "boom")); });
    expect(onError).toHaveBeenCalledWith("boom");
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5"]);
    const next = [ev("evt-1", 1.5, 2), ev("evt-5", 7, 8)];
    act(() => result.current.change(next, true));
    await settle();
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, next, "v6");
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("a shot PATCH landing on top of our successful PUT, answered first, is kept over the PUT's older answer", async () => {
    const { result, applyCoach } = setupWithDiscard();
    let finishPut: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((r) => { finishPut = r; }))
      .mockImplementation(async (_s, _n, events, version) => {
        if (version !== "v3") throw conflict();
        return coach(events, "v4");
      });
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // the server stores it as v2; its answer is delayed
    const patched = { ...coach([ev("evt-1", 1.1, 2)], "v3"), version: 7 }; // the PATCH on top: v3
    act(() => result.current.apply(patched)); // arrives first, withheld (its regions are not the base)
    await act(async () => { finishPut(coach([ev("evt-1", 1.1, 2)], "v2")); });
    // The page shows the PATCH's payload, not the PUT's older one ...
    expect(applyCoach).toHaveBeenLastCalledWith(patched);
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
    // ... and the next edit saves on v3 without a 409.
    act(() => result.current.change([ev("evt-1", 1.2, 2)], true));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.2, 2)], "v3");
  });

  // Fix round 5: a withheld response never outlives a later response that was adopted.
  const serverFrom = (chain: Record<string, string>) =>
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events, version) => {
      const next = version && chain[version];
      if (!next) throw conflict();
      return coach(events, next);
    });

  it("a foreign response adopted after a withhold supersedes the withheld one: nothing goes back to its revision (probe G)", async () => {
    const { result, applyCoach, onDiscard } = setupWithDiscard();
    let finishPut: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((r) => { finishPut = r; }));
    const edit1 = [ev("evt-1", 1.1, 2)];
    const edit2 = [ev("evt-1", 1.2, 2)];
    const edit3 = [ev("evt-1", 1.3, 2)];
    act(() => result.current.change(edit1, true));
    await settle(); // PUT1: the server stores it as v2; its answer is delayed
    serverFrom({ v4: "v5", v5: "v6" });
    vi.mocked(api.getStageCoach).mockImplementation(async () => coach(edit2, "v5")); // what a 409 would reload
    const patch1 = { ...coach(edit1, "v3"), version: 2 }; // a shot PATCH on top of PUT1, answered first
    act(() => result.current.apply(patch1)); // withheld: its regions are not the base
    act(() => result.current.change(edit2, true)); // the user commits again; waits in the debounce
    await act(async () => { finishPut(coach(edit1, "v2")); }); // PUT1's answer; the debounce keeps the list
    const patch2 = { ...coach(edit1, "v4"), version: 3 }; // a second PATCH: its regions equal the base now
    act(() => result.current.apply(patch2)); // adopted, so PUT2 goes out on v4
    await settle(); // PUT2 @v4 -> v5
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, edit2, "v4");
    // Settled: the page shows PUT2's answer, never the withheld v3 ...
    expect(applyCoach).toHaveBeenLastCalledWith(coach(edit2, "v5"));
    expect(result.current.events).toEqual(edit2);
    // ... and the next edit saves on v5, with no 409 and nothing discarded.
    act(() => result.current.change(edit3, true));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(3);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, edit3, "v5");
    expect(api.getStageCoach).not.toHaveBeenCalled();
    expect(onDiscard).not.toHaveBeenCalled();
    expect(result.current.events).toEqual(edit3);
  });

  it("a withheld response that survived a failed 409 reload is superseded by the next idle response", async () => {
    const { result, applyCoach, onError, onDiscard } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }));
    vi.mocked(api.getStageCoach).mockRejectedValueOnce(new ApiError(500, "boom"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // in flight on v1
    act(() => result.current.apply(withEvt5())); // withheld at v6
    await act(async () => { failFirst(conflict()); }); // the reload fails: the withheld response stays
    expect(onError).toHaveBeenCalledWith("boom");
    // Nothing is outstanding; a later response (a reclassify, say) is the newest state there is.
    const later = { ...coach([ev("evt-1", 1, 2), ev("evt-5", 7, 8), ev("evt-6", 9, 9.5)], "v8"), version: 3 };
    act(() => result.current.apply(later));
    expect(applyCoach).toHaveBeenLastCalledWith(later);
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5", "evt-6"]);
    serverFrom({ v8: "v9" });
    const next = [ev("evt-1", 1.5, 2), ev("evt-5", 7, 8), ev("evt-6", 9, 9.5)];
    act(() => result.current.change(next, true));
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, next, "v8");
    expect(api.getStageCoach).toHaveBeenCalledTimes(1);
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("a 409's reload supersedes a withheld response: the next edit saves on the reload's revision", async () => {
    const { result } = setupWithDiscard();
    let failFirst: (e: unknown) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((_r, j) => { failFirst = j; }))
      .mockImplementation(async (_s, _n, events, version) => {
        if (version !== "v8") throw conflict();
        return coach(events, "v9");
      });
    const newer = coach([ev("evt-1", 1, 2), ev("evt-5", 7, 8), ev("evt-6", 9, 9.5)], "v8");
    vi.mocked(api.getStageCoach).mockResolvedValue(newer);
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // in flight on v1
    act(() => result.current.apply(withEvt5())); // withheld at v6
    await act(async () => { failFirst(conflict()); }); // reload: v8, discard
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5", "evt-6"]);
    const next = [ev("evt-1", 1.5, 2), ev("evt-5", 7, 8), ev("evt-6", 9, 9.5)];
    act(() => result.current.change(next, true));
    await settle();
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, next, "v8");
    expect(result.current.events).toEqual(next);
  });

  it("a commit made during a 409's reload, then a drag and Esc, is sent once", async () => {
    const { result } = setupWithDiscard();
    let finishReload: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockRejectedValueOnce(conflict())
      .mockImplementation(async (_s, _n, events) => coach(events, "v10"));
    vi.mocked(api.getStageCoach).mockImplementationOnce(() => new Promise((r) => { finishReload = r; }));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle(); // PUT 409s, the reload is in flight
    act(() => result.current.change([ev("evt-1", 1.2, 2)], true)); // waits in the debounce
    act(() => result.current.change([ev("evt-1", 1.2, 2.5)], false)); // a drag starts
    await act(async () => { finishReload(coach([ev("evt-1", 1, 2)], "v9")); }); // regions unchanged
    act(() => result.current.change([ev("evt-1", 1.2, 2)], false));
    act(() => result.current.cancel());
    await settle();
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.2, 2)], "v9");
  });

  it("a 409 whose reload changed the regions discards the edit without re-sending and reports it", async () => {
    const { result, onDiscard } = setupWithDiscard();
    vi.mocked(api.putStageEvents).mockRejectedValueOnce(conflict());
    vi.mocked(api.getStageCoach).mockResolvedValue(coach([ev("evt-1", 1, 2.5)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(result.current.events).toEqual([ev("evt-1", 1, 2.5)]);
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });
});

describe("useStageEvents driving the LaneEditor", () => {
  const applyCoach = vi.fn();
  const onError = vi.fn();
  function Page() {
    const s = useStageEvents("anna", 1, applyCoach, onError);
    const { apply } = s;
    useEffect(() => apply(coach([ev("evt-1", 1, 2)], "v1")), [apply]);
    return (
      <LaneEditor
        shots={[]}
        events={s.events}
        stageTime={10}
        fps={50}
        currentTime={0}
        selectedId={s.selectedId}
        onSelect={s.select}
        onSeek={() => {}}
        onChange={s.change}
        onCancel={s.cancel}
      />
    );
  }

  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api.putStageEvents).mockReset();
    vi.mocked(api.getStageCoach).mockReset();
    applyCoach.mockReset();
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 1000, height: 32, left: 0, top: 0, right: 1000, bottom: 32, x: 0, y: 0, toJSON: () => ({}),
    });
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
    HTMLElement.prototype.releasePointerCapture = vi.fn();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("a region being drawn survives a PUT that resolves mid-gesture and is in the committed list", async () => {
    let finishNudge: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents)
      .mockImplementationOnce(() => new Promise((r) => { finishNudge = r; }))
      .mockImplementationOnce(async (_slug, _stage, events) => coach(events, "v3"));
    render(<Page />);
    // A nudge commits; its PUT is still in flight when the next gesture starts.
    fireEvent.click(screen.getByTestId("event-evt-1"));
    fireEvent.keyDown(screen.getByTestId("event-evt-1"), { key: "ArrowRight" });
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);

    const lane = screen.getByTestId("lane-reload");
    fireEvent.pointerDown(lane, { pointerId: 1, clientX: 400, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 450, clientY: 10 });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 540, clientY: 10 });
    await act(async () => { finishNudge(coach([ev("evt-1", 1.02, 2)], "v2")); });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 560, clientY: 10 });
    fireEvent.pointerUp(lane, { pointerId: 1, clientX: 560, clientY: 10 });
    await settle();

    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    const [, , committed, revision] = vi.mocked(api.putStageEvents).mock.calls[1];
    expect(revision).toBe("v2");
    const drawn = committed.find((e) => e.kind === "reload");
    expect(drawn).toBeDefined();
    expect(drawn!.start).toBeCloseTo(4.0, 2);
    expect(drawn!.end).toBeCloseTo(5.6, 2);
    expect(screen.getByTestId(`event-${drawn!.id}`)).toBeInTheDocument();
  });

  it("Esc during a drag ends the live state: a PUT landing afterwards is applied in full", async () => {
    let finishNudge: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((r) => { finishNudge = r; }));
    render(<Page />);
    fireEvent.click(screen.getByTestId("event-evt-1"));
    fireEvent.keyDown(screen.getByTestId("event-evt-1"), { key: "ArrowRight" });
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);

    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 2, clientX: 200, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 300, clientY: 10, altKey: true });
    fireEvent.keyDown(window, { key: "Escape" });
    // The server's list must show once the gesture is over, not wait for the
    // next commit (evt-7 stands in for anything the restored list lacks).
    const answer = coach([ev("evt-1", 1.02, 2), ev("evt-7", 8, 9)], "v2");
    await act(async () => { finishNudge(answer); });
    expect(applyCoach).toHaveBeenLastCalledWith(answer);
    expect(screen.getByTestId("event-evt-7")).toBeInTheDocument();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
  });
});

describe("useStageEvents: a save problem is an issue the page shows, with a retry", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api.putStageEvents).mockReset();
    vi.mocked(api.getStageCoach).mockReset();
  });
  afterEach(() => vi.useRealTimers());

  const conflict = () => new ApiError(409, "version_conflict", { code: "version_conflict" });
  const boom = () => new ApiError(500, "Internal Server Error", {});

  function setupIssue() {
    const applyCoach = vi.fn();
    const onError = vi.fn();
    const onDiscard = vi.fn();
    const hook = renderHook(() => useStageEvents("anna", 1, applyCoach, onError, onDiscard));
    act(() => hook.result.current.apply(coach([ev("evt-1", 1, 2)], "v1")));
    return { ...hook, applyCoach, onError, onDiscard };
  }

  it("a 500 is a failed issue; retry re-sends the failed list and its success clears the issue", async () => {
    const { result, onError } = setupIssue();
    vi.mocked(api.putStageEvents)
      .mockRejectedValueOnce(boom())
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v2"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    expect(result.current.issue).toEqual({ kind: "failed", message: "Internal Server Error" });
    expect(onError).toHaveBeenCalledTimes(1);
    // The list caught up with the server's, as before: the retry brings the edit back.
    expect(result.current.events).toEqual([ev("evt-1", 1, 2)]);
    await act(async () => { result.current.retry(); });
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.1, 2)], "v1");
    expect(result.current.issue).toBeNull();
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
  });

  it("a failed retry keeps the issue and the list to retry again", async () => {
    const { result } = setupIssue();
    vi.mocked(api.putStageEvents).mockRejectedValueOnce(boom()).mockRejectedValueOnce(boom())
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v2"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    await act(async () => { result.current.retry(); });
    expect(result.current.issue?.kind).toBe("failed");
    await act(async () => { result.current.retry(); });
    expect(api.putStageEvents).toHaveBeenCalledTimes(3);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.1, 2)], "v1");
    expect(result.current.issue).toBeNull();
  });

  it("a 409 whose reload fails is a failed issue, and retry goes back through the 409 rules", async () => {
    const { result, onDiscard } = setupIssue();
    vi.mocked(api.putStageEvents)
      .mockRejectedValueOnce(conflict())
      .mockRejectedValueOnce(conflict())
      .mockImplementationOnce(async (_s, _n, events) => coach(events, "v10"));
    vi.mocked(api.getStageCoach)
      .mockRejectedValueOnce(new ApiError(503, "Service Unavailable", {}))
      // The revision moved for a shot PATCH: the regions are as the edit found them.
      .mockResolvedValueOnce(coach([ev("evt-1", 1, 2)], "v9"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    expect(result.current.issue).toEqual({ kind: "failed", message: "Service Unavailable" });
    await act(async () => { result.current.retry(); });
    await settle();
    // The retry 409'd on the stale revision, reloaded, and re-sent once on v9.
    expect(api.putStageEvents).toHaveBeenCalledTimes(3);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.1, 2)], "v9");
    expect(result.current.issue).toBeNull();
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("a 409 whose reload fails reverts the list to the server's, and retry puts the edit back", async () => {
    const { result } = setupIssue();
    vi.mocked(api.putStageEvents).mockRejectedValueOnce(conflict()).mockImplementationOnce(() => new Promise(() => {}));
    vi.mocked(api.getStageCoach).mockRejectedValueOnce(new ApiError(503, "Service Unavailable", {}));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    // Not saved, so not shown as saved.
    expect(result.current.events).toEqual([ev("evt-1", 1, 2)]);
    act(() => result.current.retry());
    expect(result.current.events).toEqual([ev("evt-1", 1.1, 2)]);
  });

  it("busy follows an outstanding edit: a drag, the debounce, the PUT in flight", async () => {
    const { result } = setupIssue();
    let finish: (c: CoachStageResponse) => void = () => {};
    vi.mocked(api.putStageEvents).mockImplementationOnce(() => new Promise((r) => { finish = r; }));
    expect(result.current.busy).toBe(false);
    act(() => result.current.change([ev("evt-1", 1.05, 2)], false));
    expect(result.current.busy).toBe(true);
    act(() => result.current.change([ev("evt-1", 1, 2)], false));
    act(() => result.current.cancel());
    expect(result.current.busy).toBe(false);
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    expect(result.current.busy).toBe(true);
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(result.current.busy).toBe(true);
    await act(async () => { finish(coach([ev("evt-1", 1.1, 2)], "v2")); });
    expect(result.current.busy).toBe(false);
  });

  it("retry after another writer's regions arrived discards instead of overwriting them", async () => {
    const { result, onDiscard } = setupIssue();
    vi.mocked(api.putStageEvents).mockRejectedValueOnce(boom());
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    // A foreign response (a shot PATCH answered after another tab moved a region).
    act(() => result.current.apply(coach([ev("evt-1", 1, 2), ev("evt-5", 7, 8)], "v5")));
    act(() => result.current.retry());
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(result.current.issue).toEqual({ kind: "discarded" });
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(result.current.events.map((e) => e.id)).toEqual(["evt-1", "evt-5"]);
  });

  it("retry does nothing while an edit is outstanding", async () => {
    const { result } = setupIssue();
    vi.mocked(api.putStageEvents).mockRejectedValueOnce(boom())
      .mockImplementation(async (_s, _n, events) => coach(events, "v2"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    act(() => result.current.change([ev("evt-1", 1.3, 2)], true)); // in the debounce
    act(() => result.current.retry());
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
    expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [ev("evt-1", 1.3, 2)], "v1");
    // The newer edit saved, so the issue is gone and a late retry has nothing to send.
    expect(result.current.issue).toBeNull();
    act(() => result.current.retry());
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(2);
  });

  it("a discard is one issue however often it is reported; dismiss clears it and gives up a failed list", async () => {
    const { result, onDiscard } = setupIssue();
    vi.mocked(api.putStageEvents).mockRejectedValueOnce(conflict()).mockRejectedValueOnce(conflict());
    vi.mocked(api.getStageCoach)
      .mockResolvedValueOnce(coach([ev("evt-1", 1, 2), ev("evt-5", 7, 8)], "v6"))
      .mockResolvedValueOnce(coach([ev("evt-1", 1, 2), ev("evt-6", 9, 10)], "v7"));
    act(() => result.current.change([ev("evt-1", 1.1, 2)], true));
    await settle();
    act(() => result.current.change([ev("evt-1", 1.2, 2)], true));
    await settle();
    expect(onDiscard).toHaveBeenCalledTimes(2);
    expect(result.current.issue).toEqual({ kind: "discarded" });
    act(() => result.current.dismiss());
    expect(result.current.issue).toBeNull();
    vi.mocked(api.putStageEvents).mockRejectedValueOnce(boom());
    act(() => result.current.change([ev("evt-1", 1.3, 2)], true));
    await settle();
    expect(result.current.issue?.kind).toBe("failed");
    act(() => result.current.dismiss());
    expect(result.current.issue).toBeNull();
    // Dismissed is given up: retry has nothing to send.
    act(() => result.current.retry());
    await settle();
    expect(api.putStageEvents).toHaveBeenCalledTimes(3);
  });
});
