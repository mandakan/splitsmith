/**
 * useStageEvents: the Coach page's region state and its writes. Every
 * PUT appends an audit event server-side, so drag frames never write, a
 * burst of commits is one PUT, and a commit waits for the one in flight
 * so it carries the revision that one returned.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

  it("unmounting flushes a pending commit instead of dropping it", async () => {
    const { result, unmount } = setup();
    vi.mocked(api.putStageEvents).mockResolvedValue(coach([], "v2"));
    act(() => result.current.change([], true));
    unmount();
    await act(async () => {});
    expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [], "v1");
  });
});
