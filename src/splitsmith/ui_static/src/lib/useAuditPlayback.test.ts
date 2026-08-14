import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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

describe("useAuditPlayback", () => {
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
});
