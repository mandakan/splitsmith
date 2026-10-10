/**
 * The primary's sound while a secondary is big, against fake media: the
 * big player is muted only while the follower runs, an audio error hands
 * the sound back to the big player, and the follower keeps the beep
 * offset through seeks, play and pause.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PIP_DRIFT_PLAYING_S } from "./pip";
import {
  LEAD_MAX_S,
  LEAD_MAX_SEEKS,
  LEAD_MEASURE_MS,
  LEAD_MIN_S,
  audioLag,
  lagNeedsLead,
  seekLead,
  usePrimaryAudio,
} from "./usePrimaryAudio";

class FakeMedia extends EventTarget {
  t = 0;
  paused = true;
  playbackRate = 1;
  muted = false;
  duration = 60;
  readyState = 4;
  seeking = false;
  ended = false;
  preload = "";
  src = "";
  seeks = 0;
  get currentTime() {
    return this.t;
  }
  set currentTime(v: number) {
    this.seeks++;
    this.t = v;
  }
  play() {
    this.paused = false;
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
  removeAttribute(name: string) {
    if (name === "src") this.src = "";
  }
  fire(type: string) {
    this.dispatchEvent(new Event(type));
  }
}

let audios: FakeMedia[] = [];

beforeEach(() => {
  audios = [];
  vi.stubGlobal(
    "Audio",
    class extends FakeMedia {
      constructor() {
        super();
        audios.push(this);
      }
    },
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

type Args = Parameters<typeof usePrimaryAudio>[0];

function setup(over: Partial<Args> = {}) {
  const big = new FakeMedia();
  const onError = vi.fn();
  const base: Args = {
    bigVideo: big as unknown as HTMLMediaElement,
    bigIsPrimary: false,
    src: "/primary.mp4",
    primaryBeep: 5,
    bigBeep: 12,
    onError,
    ...over,
  };
  const hook = renderHook((props: Args) => usePrimaryAudio(props), { initialProps: base });
  const rerender = (more: Partial<Args>) => hook.rerender({ ...base, ...more });
  return { big, onError, hook, rerender };
}

describe("usePrimaryAudio", () => {
  it("does nothing while the primary is big", () => {
    const { big, hook } = setup({ bigIsPrimary: true });
    expect(audios).toHaveLength(0);
    expect(big.muted).toBe(false);
    expect(hook.result.current.active).toBe(false);
  });

  it("mutes the big player and plays the primary's stream, unmuted, while a secondary is big", () => {
    const { big, hook } = setup();
    expect(hook.result.current.active).toBe(true);
    expect(audios).toHaveLength(1);
    expect(audios[0].src).toBe("/primary.mp4");
    expect(audios[0].muted).toBe(false);
    expect(big.muted).toBe(true);
  });

  it("unmutes the big player and drops the audio on swap back", () => {
    const { big, rerender } = setup();
    const audio = audios[0];
    rerender({ bigIsPrimary: true });
    expect(big.muted).toBe(false);
    expect(audio.src).toBe("");
    audio.seeks = 0;
    big.t = 20;
    big.fire("seeking");
    expect(audio.seeks).toBe(0); // detached
  });

  it("an audio error unmutes the big player and reports it; the next stream starts afresh", () => {
    const { big, onError, rerender } = setup();
    act(() => audios[0].fire("error"));
    expect(onError).toHaveBeenCalledTimes(1);
    expect(big.muted).toBe(false);
    expect(audios[0].src).toBe("");
    rerender({ src: "/primary-trim.mp4" });
    expect(audios).toHaveLength(2);
    expect(audios[1].src).toBe("/primary-trim.mp4");
    expect(big.muted).toBe(true);
  });

  it("nothing left to try (no src) leaves the big player's own sound on", () => {
    const { big } = setup({ src: null });
    expect(audios).toHaveLength(0);
    expect(big.muted).toBe(false);
  });

  it("follows seeks, play and pause on the beep offset (primary beep 5, big beep 12)", () => {
    const { big } = setup();
    const audio = audios[0];
    big.t = 15; // 3 s after the beep
    big.fire("seeking");
    expect(audio.t).toBe(8);
    big.paused = false;
    big.fire("play");
    expect(audio.paused).toBe(false);
    big.paused = true;
    big.fire("pause");
    expect(audio.paused).toBe(true);
  });

  it("leaves a small gap alone while playing: a seek of playing audio costs more than it closes", () => {
    const { big } = setup();
    const audio = audios[0];
    big.paused = false;
    big.t = 15;
    big.fire("play");
    audio.t = 8 - 0.05; // the measured play-start lag
    audio.seeks = 0;
    big.fire("timeupdate");
    expect(audio.seeks).toBe(0);
    audio.t = 8 - PIP_DRIFT_PLAYING_S - 0.05;
    big.fire("timeupdate");
    expect(audio.t).toBe(8);
  });

  it("unmutes on unmount", () => {
    const { big, hook } = setup();
    hook.unmount();
    expect(big.muted).toBe(false);
  });
});

/** A media pair with a wall clock: the big video plays from 15 (3 s after
 *  its beep at 12, the audio's moment 8); the audio starts ``startLag``
 *  late, and a seek of it resumes ``seekCost`` after the seek, as
 *  measured (an MP4 trim: start lag ~50 ms, seek cost ~100-140 ms). */
describe("usePrimaryAudio lead correction", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function playing(opts: { startLag: number; seekCost: number; alreadyPlaying?: boolean }) {
    let wall = 0;
    const big = new FakeMedia();
    // The audio's position is a function of the wall clock: where it was
    // (re)started and when it actually starts moving.
    let from = { wall: 0, t: 8, resume: opts.startLag };
    const seekLog: number[] = [];
    const startPlay = () => {
      big.t = 15;
      big.paused = false;
      wall = 0;
    };
    if (opts.alreadyPlaying) startPlay();
    const hook = renderHook(() =>
      usePrimaryAudio({
        bigVideo: big as unknown as HTMLMediaElement,
        bigIsPrimary: false,
        src: "/p.mp4",
        primaryBeep: 5,
        bigBeep: 12,
      }),
    );
    const audio = audios[0];
    Object.defineProperty(audio, "currentTime", {
      configurable: true,
      get: () => from.t + Math.max(0, wall - from.wall - from.resume),
      set: (v: number) => {
        seekLog.push(v);
        from = { wall, t: v, resume: opts.seekCost };
      },
    });
    if (!opts.alreadyPlaying) {
      startPlay();
      big.fire("play");
    }
    audio.paused = false;
    from = { wall: 0, t: 8, resume: opts.startLag };
    seekLog.length = 0;
    const advance = (ms: number) => {
      for (let i = 0; i < ms; i++) {
        wall += 0.001;
        big.t = 15 + wall;
        vi.advanceTimersByTime(1);
      }
    };
    const lagNow = () => big.t - 12 - (audio.currentTime - 5);
    /** A new play start: the audio sits on the picture and starts late. */
    const restart = (startLag: number) => {
      from = { wall, t: big.t - 12 + 5, resume: startLag };
    };
    return { big, audio, hook, seekLog, advance, lagNow, restart };
  }

  it("measures once LEAD_MEASURE_MS after the play start, never per frame", () => {
    const { seekLog, advance } = playing({ startLag: 0.05, seekCost: 0.05 });
    advance(LEAD_MEASURE_MS - 1);
    expect(seekLog).toHaveLength(0);
    advance(1);
    expect(seekLog).toHaveLength(1);
    // Seek cost equal to the lag: one correction lands it.
    advance(3 * LEAD_MEASURE_MS);
    expect(seekLog).toHaveLength(1);
  });

  it("lands an MP4's dearer seek by learning its cost from the residual", () => {
    const { seekLog, advance, lagNow } = playing({ startLag: 0.05, seekCost: 0.12 });
    advance(LEAD_MEASURE_MS + 200);
    expect(lagNow()).toBeCloseTo(0.12 - 0.05, 3); // led by the lag: still behind
    advance(LEAD_MEASURE_MS);
    expect(seekLog).toHaveLength(2); // led by the learned cost
    advance(LEAD_MEASURE_MS);
    expect(Math.abs(lagNow())).toBeLessThan(LEAD_MIN_S);
    advance(3 * LEAD_MEASURE_MS);
    expect(seekLog).toHaveLength(2);
  });

  it("keeps the learned cost for the next play start", () => {
    const { big, seekLog, advance, lagNow, restart } = playing({ startLag: 0.05, seekCost: 0.12 });
    advance(3 * LEAD_MEASURE_MS);
    expect(seekLog).toHaveLength(2);
    big.paused = true;
    big.fire("pause");
    big.paused = false;
    big.fire("play");
    const before = seekLog.length; // the sync's own seeks on pause and play
    restart(0.06);
    advance(LEAD_MEASURE_MS + 300);
    expect(seekLog.length - before).toBe(1); // one seek, at the learned lead
    expect(Math.abs(lagNow())).toBeLessThan(LEAD_MIN_S);
  });

  it("stops after LEAD_MAX_SEEKS corrections per play start", () => {
    // A stream whose seek cost cannot be led (past the clamp) keeps a lag.
    const { seekLog, advance } = playing({ startLag: 0.05, seekCost: LEAD_MAX_S + 0.1 });
    advance(10 * LEAD_MEASURE_MS);
    expect(seekLog.length).toBeLessThanOrEqual(LEAD_MAX_SEEKS);
  });

  it("leaves a lag under LEAD_MIN_S alone", () => {
    const { seekLog, advance } = playing({ startLag: LEAD_MIN_S / 2, seekCost: 0.1 });
    advance(2 * LEAD_MEASURE_MS);
    expect(seekLog).toHaveLength(0);
  });

  it("a pause before the measurement cancels it", () => {
    const { big, seekLog, advance } = playing({ startLag: 0.07, seekCost: 0.07 });
    advance(100);
    big.paused = true;
    big.fire("pause");
    advance(2 * LEAD_MEASURE_MS);
    // Only the sync's own forced seek on pause (onto the picture, 8.1).
    expect(seekLog).toEqual([8.1]);
  });

  it("measures again after a seek of the big video while playing", () => {
    const { big, seekLog, advance } = playing({ startLag: 0.05, seekCost: 0.05 });
    advance(2 * LEAD_MEASURE_MS);
    expect(seekLog).toHaveLength(1);
    big.fire("seeking"); // the sync seeks the audio onto the picture
    big.fire("seeked");
    const after = seekLog.length;
    advance(LEAD_MEASURE_MS);
    expect(seekLog.length).toBe(after + 1);
  });

  it("measures when swapped in while the big video already plays", () => {
    const { seekLog, advance } = playing({ startLag: 0.05, seekCost: 0.05, alreadyPlaying: true });
    advance(LEAD_MEASURE_MS);
    expect(seekLog).toHaveLength(1);
  });
});

describe("lead rules", () => {
  it("audioLag maps both clocks through their own beep", () => {
    expect(audioLag({ bigTime: 15, bigBeep: 12, audioTime: 7.95, primaryBeep: 5 })).toBeCloseTo(0.05, 9);
    expect(audioLag({ bigTime: 15, bigBeep: 12, audioTime: 8.03, primaryBeep: 5 })).toBeCloseTo(-0.03, 9);
  });

  it("lagNeedsLead leaves tiny lags, and gaps the drift rule owns, alone", () => {
    expect(lagNeedsLead(LEAD_MIN_S / 2)).toBe(false);
    expect(lagNeedsLead(0.05)).toBe(true);
    expect(lagNeedsLead(-0.05)).toBe(true);
    expect(lagNeedsLead(0.5)).toBe(false);
  });

  it("seekLead is the lag until a cost is learned, never past the clamp", () => {
    expect(seekLead(0.05, null)).toBe(0.05);
    expect(seekLead(0.05, 0.12)).toBe(0.12);
    expect(seekLead(0.05, 1)).toBe(LEAD_MAX_S);
  });
});
