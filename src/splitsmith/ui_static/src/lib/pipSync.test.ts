/**
 * The inset clock against fake media that behaves like the real thing
 * where it matters: ``ended`` at the clip's end, ``play()`` on ended
 * media restarting at 0, ``readyState`` and ``seeking``. Each case names
 * the defect or the clause it pins (review of #1406 against two real
 * clips in headless Chromium).
 */
import { describe, expect, it } from "vitest";

import { PIP_END_GUARD_S } from "./pip";
import { attachInsetSync } from "./pipSync";

class FakeMedia extends EventTarget {
  t = 0;
  paused = true;
  playbackRate = 1;
  muted = false;
  duration = NaN;
  readyState = 4;
  seeking = false;
  seeks = 0;
  plays = 0;

  get currentTime() {
    return this.t;
  }
  set currentTime(v: number) {
    this.seeks++;
    this.t = v;
  }
  get ended() {
    return Number.isFinite(this.duration) && this.t >= this.duration;
  }
  play() {
    // HTML: play() on ended media seeks to the start first.
    if (this.ended) this.t = 0;
    this.plays++;
    this.paused = false;
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
  fire(type: string) {
    this.dispatchEvent(new Event(type));
  }
}

function setup(opts: { bigBeep?: number; insetBeep?: number; insetDuration?: number; insetReady?: number } = {}) {
  const big = new FakeMedia();
  big.duration = 60;
  const inset = new FakeMedia();
  inset.duration = opts.insetDuration ?? 60;
  inset.readyState = opts.insetReady ?? 4;
  const detach = attachInsetSync(big as unknown as HTMLVideoElement, inset as unknown as HTMLVideoElement, {
    bigBeep: opts.bigBeep ?? 5,
    insetBeep: opts.insetBeep ?? 3,
  });
  const playBig = () => {
    big.paused = false;
    big.fire("play");
  };
  return { big, inset, detach, playBig };
}

describe("attachInsetSync", () => {
  it("mutes the inset", () => {
    const { inset } = setup();
    expect(inset.muted).toBe(true);
  });

  it("leaves an audible follower's sound on and still keeps it on the big clock (#1409)", () => {
    const big = new FakeMedia();
    big.t = 9;
    const audio = new FakeMedia();
    attachInsetSync(big as unknown as HTMLMediaElement, audio as unknown as HTMLMediaElement, { bigBeep: 5, insetBeep: 2 }, { audible: true });
    expect(audio.muted).toBe(false);
    expect(audio.currentTime).toBe(6);
  });

  it("lines the inset up at once when it already has metadata", () => {
    const big = new FakeMedia();
    big.t = 9;
    const inset = new FakeMedia();
    attachInsetSync(big as unknown as HTMLVideoElement, inset as unknown as HTMLVideoElement, { bigBeep: 5, insetBeep: 3 });
    expect(inset.t).toBe(7);
  });

  it("lines the inset up on loadedmetadata when it had none", () => {
    const { big, inset } = setup({ insetReady: 0 });
    big.t = 9;
    big.fire("timeupdate");
    expect(inset.seeks).toBe(0);
    inset.readyState = 1;
    inset.fire("loadedmetadata");
    expect(inset.t).toBe(7);
  });

  it("follows a seek, play, pause and rate through the beep offsets", () => {
    const { big, inset, playBig } = setup();
    big.t = 9;
    big.fire("seeking");
    expect(inset.t).toBe(7);
    playBig();
    expect(inset.paused).toBe(false);
    big.playbackRate = 0.5;
    big.fire("ratechange");
    expect(inset.playbackRate).toBe(0.5);
    big.paused = true;
    big.fire("pause");
    expect(inset.paused).toBe(true);
  });

  it("starts the inset on timeupdate when the big plays and the inset does not", () => {
    const { big, inset } = setup();
    big.t = 9;
    big.paused = false; // no play event reached us
    big.fire("timeupdate");
    expect(inset.paused).toBe(false);
  });

  it("corrects drift past 0.2 s while playing and leaves less alone", () => {
    const { big, inset, playBig } = setup();
    playBig();
    big.t = 10; // inset target 8
    inset.t = 8.1;
    inset.seeks = 0;
    big.fire("timeupdate");
    expect(inset.seeks).toBe(0);
    inset.t = 8.5;
    big.fire("timeupdate");
    expect(inset.t).toBe(8);
  });

  it("holds a paused inset to the instant: 0.1 s off is corrected when the big is paused", () => {
    const { big, inset } = setup();
    big.t = 10;
    inset.t = 8.1;
    inset.seeks = 0;
    big.fire("timeupdate");
    expect(inset.t).toBe(8);
  });

  it("re-checks drift after the big's seeked", () => {
    const { big, inset } = setup();
    big.t = 10;
    inset.t = 3;
    big.fire("seeked");
    expect(inset.t).toBe(8);
  });

  it("A3: a seek is one inset seek, not two (seeked only re-checks)", () => {
    const { big, inset } = setup();
    inset.seeks = 0;
    for (let i = 0; i < 10; i++) {
      big.t = 6 + i * 0.05;
      big.fire("seeking");
      big.fire("seeked");
    }
    expect(inset.seeks).toBe(10);
  });

  it("A4: no drift correction while the inset is still seeking", () => {
    const { big, inset, playBig } = setup();
    playBig();
    big.t = 10;
    inset.t = 3;
    inset.seeking = true;
    inset.seeks = 0;
    big.fire("timeupdate");
    expect(inset.seeks).toBe(0);
    inset.seeking = false;
    big.fire("timeupdate");
    expect(inset.t).toBe(8);
  });

  it("pause re-seeks the held instant even while the inset is seeking", () => {
    const { big, inset, playBig } = setup();
    playBig();
    big.t = 10;
    inset.t = 8.1;
    inset.seeking = true;
    big.paused = true;
    big.fire("pause");
    expect(inset.t).toBe(8);
  });

  it("A1: a shorter inset holds before its end and never restarts at 0", () => {
    const { big, inset, playBig } = setup({ bigBeep: 0, insetBeep: 0, insetDuration: 4 });
    big.t = 3;
    playBig();
    expect(inset.paused).toBe(false);
    for (let t = 3.25; t <= 8; t += 0.25) {
      big.t = t;
      inset.t = Math.min(t, 4); // the inset plays on to its end
      if (inset.ended) {
        inset.paused = true;
        inset.fire("ended");
      }
      big.fire("timeupdate");
    }
    expect(inset.paused).toBe(true);
    expect(inset.t).toBeCloseTo(4 - PIP_END_GUARD_S);
    expect(inset.plays).toBe(1);
  });

  it("A1: an inset that ended a little early is re-seeked before it is played again", () => {
    const { big, inset, playBig } = setup({ bigBeep: 0, insetBeep: 0, insetDuration: 4 });
    big.t = 3;
    playBig();
    inset.t = 4; // ran ahead and ended, within the playing drift threshold
    inset.paused = true;
    big.t = 3.85;
    big.fire("timeupdate");
    expect(inset.t).toBe(3.85); // not 0: play() on ended media would restart it
    expect(inset.paused).toBe(false);
  });

  it("A2: before the inset's start it holds at 0, paused, with no seeks", () => {
    const { big, inset, playBig } = setup({ bigBeep: 3, insetBeep: 0 });
    playBig();
    inset.seeks = 0;
    for (let t = 0.25; t < 3; t += 0.25) {
      big.t = t;
      big.fire("timeupdate");
    }
    expect(inset.paused).toBe(true);
    expect(inset.t).toBe(0);
    expect(inset.seeks).toBe(0);
    big.t = 3.2;
    big.fire("timeupdate");
    expect(inset.paused).toBe(false);
  });

  it("A5: holds while the big is waiting, resumes when it plays again", () => {
    const { big, inset, playBig } = setup();
    big.t = 9;
    playBig();
    big.fire("waiting");
    expect(inset.paused).toBe(true);
    big.fire("timeupdate");
    expect(inset.paused).toBe(true);
    big.fire("playing");
    expect(inset.paused).toBe(false);
  });

  it("A5: a stalled big with data left keeps the inset playing; without, it holds", () => {
    const { big, inset, playBig } = setup();
    big.t = 9;
    playBig();
    big.readyState = 4;
    big.fire("stalled");
    expect(inset.paused).toBe(false);
    big.readyState = 2;
    big.fire("stalled");
    expect(inset.paused).toBe(true);
  });

  it("detaches every listener", () => {
    const { big, inset, detach } = setup();
    detach();
    big.t = 20;
    inset.seeks = 0;
    for (const e of ["seeking", "seeked", "timeupdate", "play", "pause"]) big.fire(e);
    expect(inset.seeks).toBe(0);
  });
});
