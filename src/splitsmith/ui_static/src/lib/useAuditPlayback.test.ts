import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LOOP_S, useAuditPlayback } from "@/lib/useAuditPlayback";

class FakeAudio {
  currentTime = 0;
  playbackRate = 1;
  preservesPitch = false;
  paused = true;
  src: string;
  constructor(src: string) {
    this.src = src;
  }
  play = vi.fn(async () => {
    this.paused = false;
  });
  pause = vi.fn(() => {
    this.paused = true;
  });
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
}

function setup() {
  const created: FakeAudio[] = [];
  const hook = renderHook(() =>
    useAuditPlayback("/audio.wav", (src) => {
      const a = new FakeAudio(src);
      created.push(a);
      return a as unknown as HTMLAudioElement;
    }),
  );
  return { hook, el: () => created[0] };
}

function setupWithRejectingPlay() {
  const created: FakeAudio[] = [];
  const playReturnedPromises: Promise<void>[] = [];
  const hook = renderHook(() =>
    useAuditPlayback("/audio.wav", (src) => {
      const a = new FakeAudio(src);
      // Mock play to return a rejected promise
      a.play = vi.fn(() => {
        const p = Promise.reject(new Error("AbortError"));
        playReturnedPromises.push(p);
        return p as Promise<void>;
      });
      created.push(a);
      return a as unknown as HTMLAudioElement;
    }),
  );
  return { hook, el: () => created[0], playReturnedPromises };
}

describe("useAuditPlayback", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("creates the element with preservesPitch on", () => {
    const { el } = setup();
    expect(el().preservesPitch).toBe(true);
  });

  it("playFrom seeks then plays; stop pauses and stays put", () => {
    const { hook, el } = setup();
    act(() => hook.result.current.playFrom(3.2));
    expect(el().currentTime).toBeCloseTo(3.2);
    expect(el().play).toHaveBeenCalled();
    act(() => hook.result.current.stop());
    expect(el().pause).toHaveBeenCalled();
    expect(hook.result.current.playhead).toBeCloseTo(3.2);
  });

  it("seek moves the playhead without playing", () => {
    const { hook, el } = setup();
    act(() => hook.result.current.seek(7.5));
    expect(hook.result.current.playhead).toBeCloseTo(7.5);
    expect(el().play).not.toHaveBeenCalled();
  });

  it("setSpeed drives playbackRate", () => {
    const { hook, el } = setup();
    act(() => hook.result.current.setSpeed(0.25));
    expect(el().playbackRate).toBe(0.25);
  });

  it("toggleLoop anchors a centred LOOP_S region and clamps at zero", () => {
    const { hook } = setup();
    act(() => hook.result.current.toggleLoop(0.3));
    expect(hook.result.current.loop).toEqual({ start: 0, end: expect.closeTo(LOOP_S, 5) });
    act(() => hook.result.current.toggleLoop(0.3));
    expect(hook.result.current.loop).toBeNull();
  });

  it("the loop region does not move when the playhead is seeked inside it", () => {
    const { hook } = setup();
    act(() => hook.result.current.toggleLoop(5.0));
    const before = hook.result.current.loop;
    act(() => hook.result.current.seek(5.3));
    expect(hook.result.current.loop).toEqual(before);
  });

  it("sets playing to false when audio reaches the end naturally", () => {
    const { hook, el } = setup();
    const audio = el();
    // Mock play to set paused back to true immediately (simulating end-of-clip)
    audio.play = vi.fn(async () => {
      audio.paused = true;
    });
    act(() => hook.result.current.playFrom(0));
    expect(hook.result.current.playing).toBe(true);
    // Advance timers to let tick run and observe paused=true
    act(() => {
      vi.runAllTimers();
    });
    expect(hook.result.current.playing).toBe(false);
  });

  it("resets playhead, playing and loop when src changes", () => {
    const created: FakeAudio[] = [];
    const hook = renderHook(
      ({ src }) => useAuditPlayback(src, (s) => {
        const a = new FakeAudio(s);
        created.push(a);
        return a as unknown as HTMLAudioElement;
      }),
      { initialProps: { src: "/audio1.wav" } },
    );
    // Set up state on first audio
    act(() => hook.result.current.playFrom(2.5));
    act(() => hook.result.current.toggleLoop(5.0));
    act(() => hook.result.current.setSpeed(0.5));
    expect(hook.result.current.playhead).toBeCloseTo(2.5);
    expect(hook.result.current.loop).not.toBeNull();
    expect(hook.result.current.speed).toBe(0.5);
    // Change src
    act(() => hook.rerender({ src: "/audio2.wav" }));
    // Playhead, playing, and loop should reset
    expect(hook.result.current.playhead).toBeCloseTo(0);
    expect(hook.result.current.playing).toBe(false);
    expect(hook.result.current.loop).toBeNull();
    // Speed should be preserved, and applied to the new element
    expect(hook.result.current.speed).toBe(0.5);
    expect(created[1].playbackRate).toBe(0.5);
  });

  it("swallows the play() rejection from a rapid pause", () => {
    // Spy on Promise.prototype.catch to verify it's called on the rejected promise
    const catchSpy = vi.spyOn(Promise.prototype, "catch");
    try {
      const { hook, playReturnedPromises } = setupWithRejectingPlay();
      act(() => hook.result.current.playFrom(1.0));
      // Verify that play was called and returned a rejected promise
      expect(playReturnedPromises.length).toBeGreaterThan(0);
      // With the .catch() fix, Promise.prototype.catch is called
      expect(catchSpy).toHaveBeenCalled();
    } finally {
      catchSpy.mockRestore();
    }
  });
});
