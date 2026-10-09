import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FOLLOW_PLAYHEAD_KEY, WHEEL_ZOOMS_KEY, resetTimelinePrefsForTests, useFollowPlayhead, useWheelZooms } from "./timelinePrefs";

afterEach(() => {
  window.localStorage.clear();
  resetTimelinePrefsForTests();
});

describe("timeline prefs", () => {
  it("defaults: wheel zoom off, follow playhead on", () => {
    expect(renderHook(() => useWheelZooms()).result.current[0]).toBe(false);
    expect(renderHook(() => useFollowPlayhead()).result.current[0]).toBe(true);
  });

  it("persists and is shared by every mounted hook", () => {
    const a = renderHook(() => useWheelZooms());
    const b = renderHook(() => useWheelZooms());
    act(() => a.result.current[1](true));
    expect(b.result.current[0]).toBe(true);
    expect(window.localStorage.getItem(WHEEL_ZOOMS_KEY)).toBe("on");
    const f = renderHook(() => useFollowPlayhead());
    act(() => f.result.current[1](false));
    expect(window.localStorage.getItem(FOLLOW_PLAYHEAD_KEY)).toBe("off");
  });

  it("reads a stored value", () => {
    window.localStorage.setItem(WHEEL_ZOOMS_KEY, "on");
    window.localStorage.setItem(FOLLOW_PLAYHEAD_KEY, "off");
    resetTimelinePrefsForTests();
    expect(renderHook(() => useWheelZooms()).result.current[0]).toBe(true);
    expect(renderHook(() => useFollowPlayhead()).result.current[0]).toBe(false);
  });
});
