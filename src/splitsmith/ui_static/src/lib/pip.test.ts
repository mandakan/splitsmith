import { describe, expect, it } from "vitest";

import {
  containedFrame,
  cyclePip,
  DEFAULT_PIP_CORNER,
  initialPip,
  insetBox,
  insetSize,
  insetStream,
  insetTime,
  isPipCorner,
  normalizePip,
  pipCounter,
  pipKeyAction,
  shouldCorrectDrift,
  snapCorner,
  swapPip,
  type PipCamera,
} from "./pip";

const cam = (n: number, extra: Partial<PipCamera> = {}): PipCamera => ({
  id: `c${n}`,
  label: `Cam ${n}`,
  primary: n === 1,
  beepInClip: 5,
  src: `/c${n}.mp4`,
  ...extra,
});
const cams = (n: number) => Array.from({ length: n }, (_, i) => cam(i + 1));

describe("initialPip", () => {
  it("puts the primary big and the next camera in the inset", () => {
    expect(initialPip(cams(3))).toEqual({ big: "c1", inset: "c2" });
  });

  it("finds the primary wherever the page lists it", () => {
    const list = [cam(2), cam(1), cam(3)];
    expect(initialPip(list)).toEqual({ big: "c1", inset: "c2" });
  });

  it("has no inset with one camera, or when the others have no beep", () => {
    expect(initialPip(cams(1))).toEqual({ big: "c1", inset: null });
    expect(initialPip([cam(1), cam(2, { beepInClip: null })])).toEqual({ big: "c1", inset: null });
  });

  it("skips a camera without a beep", () => {
    expect(initialPip([cam(1), cam(2, { beepInClip: null }), cam(3)])).toEqual({ big: "c1", inset: "c3" });
  });

  it("has no inset when the primary itself cannot be lined up", () => {
    expect(initialPip([cam(1, { beepInClip: null }), cam(2)])).toEqual({ big: "c1", inset: null });
  });

  it("is empty with no cameras", () => {
    expect(initialPip([])).toEqual({ big: "", inset: null });
  });
});

describe("swapPip", () => {
  it("trades the big camera and the inset", () => {
    expect(swapPip({ big: "c1", inset: "c2" })).toEqual({ big: "c2", inset: "c1" });
  });

  it("does nothing without an inset", () => {
    const s = { big: "c1", inset: null };
    expect(swapPip(s)).toBe(s);
  });
});

describe("cyclePip", () => {
  it("with two cameras C equals a swap, both ways", () => {
    const s = { big: "c1", inset: "c2" };
    expect(cyclePip(s, cams(2), 1)).toEqual({ big: "c2", inset: "c1" });
    expect(cyclePip(s, cams(2), -1)).toEqual({ big: "c2", inset: "c1" });
  });

  it("with three cameras rotates the inset over the cameras that are not big", () => {
    const list = cams(3);
    let s = initialPip(list);
    s = cyclePip(s, list, 1);
    expect(s).toEqual({ big: "c1", inset: "c3" });
    s = cyclePip(s, list, 1);
    expect(s).toEqual({ big: "c1", inset: "c2" });
    s = cyclePip(s, list, -1);
    expect(s).toEqual({ big: "c1", inset: "c3" });
  });

  it("with four cameras walks in camera order, skipping the big one, and wraps", () => {
    const list = cams(4);
    const s0 = { big: "c3", inset: "c1" };
    const s1 = cyclePip(s0, list, 1);
    const s2 = cyclePip(s1, list, 1);
    const s3 = cyclePip(s2, list, 1);
    expect([s1, s2, s3].map((s) => s.inset)).toEqual(["c2", "c4", "c1"]);
    expect([s1, s2, s3].every((s) => s.big === "c3")).toBe(true);
    expect(cyclePip(s0, list, -1)).toEqual({ big: "c3", inset: "c4" });
  });

  it("never puts a camera without a beep in the inset", () => {
    const list = [cam(1), cam(2), cam(3, { beepInClip: null }), cam(4)];
    const s = cyclePip({ big: "c1", inset: "c2" }, list, 1);
    expect(s.inset).toBe("c4");
  });

  it("does nothing without an inset", () => {
    const s = { big: "c1", inset: null };
    expect(cyclePip(s, cams(1), 1)).toBe(s);
  });
});

describe("normalizePip", () => {
  it("keeps a valid state as is", () => {
    const s = { big: "c2", inset: "c3" };
    expect(normalizePip(s, cams(3))).toBe(s);
  });

  it("starts over when the big camera went away", () => {
    expect(normalizePip({ big: "c9", inset: "c2" }, cams(2))).toEqual({ big: "c1", inset: "c2" });
  });

  it("refills the inset when its camera went away or a camera gained its beep", () => {
    expect(normalizePip({ big: "c1", inset: "c4" }, cams(3))).toEqual({ big: "c1", inset: "c2" });
    expect(normalizePip({ big: "c1", inset: null }, cams(2))).toEqual({ big: "c1", inset: "c2" });
  });
});

describe("pipCounter", () => {
  it("is hidden with two cameras", () => {
    expect(pipCounter({ big: "c1", inset: "c2" }, cams(2))).toBeNull();
  });

  it("reads n / N in camera order with three or more", () => {
    expect(pipCounter({ big: "c1", inset: "c2" }, cams(3))).toEqual({ n: 2, total: 3 });
    expect(pipCounter({ big: "c2", inset: "c1" }, cams(3))).toEqual({ n: 1, total: 3 });
    expect(pipCounter({ big: "c1", inset: "c4" }, cams(4))).toEqual({ n: 4, total: 4 });
  });

  it("counts only the cameras that can be lined up", () => {
    const list = [cam(1), cam(2), cam(3, { beepInClip: null })];
    expect(pipCounter({ big: "c1", inset: "c2" }, list)).toBeNull();
  });
});

describe("pipKeyAction", () => {
  it("C forward, Shift+C back, nothing with a modifier or another key", () => {
    expect(pipKeyAction({ key: "c" })).toBe(1);
    expect(pipKeyAction({ key: "C", shiftKey: true })).toBe(-1);
    expect(pipKeyAction({ key: "c", metaKey: true })).toBeNull();
    expect(pipKeyAction({ key: "c", ctrlKey: true })).toBeNull();
    expect(pipKeyAction({ key: "c", altKey: true })).toBeNull();
    expect(pipKeyAction({ key: "x" })).toBeNull();
  });
});

describe("geometry", () => {
  const frame = { left: 50, top: 20, width: 648, height: 365 };

  it("finds the rendered picture of a pillarboxed and a letterboxed video", () => {
    expect(containedFrame({ left: 0, top: 0, width: 800, height: 300 }, 1920, 1080)).toEqual({
      left: (800 - 300 * (16 / 9)) / 2,
      top: 0,
      width: 300 * (16 / 9),
      height: 300,
    });
    expect(containedFrame({ left: 0, top: 0, width: 400, height: 400 }, 1600, 900)).toEqual({
      left: 0,
      top: (400 - 225) / 2,
      width: 400,
      height: 225,
    });
  });

  it("uses the box itself before the video knows its size", () => {
    const box = { left: 1, top: 2, width: 3, height: 4 };
    expect(containedFrame(box, 0, 0)).toBe(box);
  });

  it("sizes the inset at 28% of the frame, 16:9", () => {
    expect(insetSize(648)).toEqual({ width: 181, height: 102, chips: true });
    expect(insetSize(746)).toEqual({ width: 209, height: 118, chips: true });
  });

  it("is never narrower than 180 px: a larger share of a small frame", () => {
    expect(insetSize(643)).toMatchObject({ width: 180, chips: true });
    expect(insetSize(641)).toMatchObject({ width: 180, chips: true });
    // Audit at 1440 x 800: 28% would be 133 px.
    expect(insetSize(474)).toEqual({ width: 180, height: 101, chips: true });
    expect(insetSize(400)).toEqual({ width: 180, height: 101, chips: true });
  });

  it("never covers more than 45% of the frame; the chips drop once that cap bites under 180 px", () => {
    expect(insetSize(360)).toEqual({ width: 162, height: 91, chips: false });
    expect(insetSize(200)).toEqual({ width: 90, height: 51, chips: false });
    expect(insetSize(0)).toEqual({ width: 0, height: 0, chips: false });
  });

  it("places the inset 10 px in from each corner, the top ones below the pill", () => {
    expect(insetBox(frame, "tr", { topInset: 26 })).toEqual({
      left: 50 + 648 - 181 - 10,
      top: 20 + 10 + 26,
      width: 181,
      height: 102,
    });
    expect(insetBox(frame, "tl", { topInset: 26 })).toMatchObject({ left: 60, top: 56 });
    expect(insetBox(frame, "br", { topInset: 26 })).toMatchObject({ left: 507, top: 20 + 365 - 102 - 10 });
    expect(insetBox(frame, "bl", { bottomInset: 30 })).toMatchObject({
      left: 60,
      top: 20 + 365 - 102 - 10 - 30,
    });
  });

  it("snaps a drag to the corner whose quadrant holds the centre", () => {
    expect(snapCorner(frame, 60, 30)).toBe("tl");
    expect(snapCorner(frame, 600, 30)).toBe("tr");
    expect(snapCorner(frame, 60, 300)).toBe("bl");
    expect(snapCorner(frame, 600, 300)).toBe("br");
    // Dragged past the frame's edge still snaps to the near corner.
    expect(snapCorner(frame, 2000, -50)).toBe("tr");
  });

  it("defaults to top right and validates a stored corner", () => {
    expect(DEFAULT_PIP_CORNER).toBe("tr");
    expect(isPipCorner("bl")).toBe(true);
    expect(isPipCorner("middle")).toBe(false);
    expect(isPipCorner(null)).toBe(false);
  });
});

describe("clocks", () => {
  it("lines the inset up on its own beep", () => {
    expect(insetTime({ bigTime: 7, bigBeep: 5, insetBeep: 3 })).toBe(5);
    expect(insetTime({ bigTime: 7, bigBeep: 3, insetBeep: 5 })).toBe(9);
  });

  it("clamps to the inset clip's start and end", () => {
    expect(insetTime({ bigTime: 1, bigBeep: 5, insetBeep: 2 })).toBe(0);
    expect(insetTime({ bigTime: 40, bigBeep: 5, insetBeep: 5, insetDuration: 30 })).toBe(30);
    expect(insetTime({ bigTime: 40, bigBeep: 5, insetBeep: 5, insetDuration: NaN })).toBe(40);
  });

  it("corrects drift past 0.2 s while playing, under a frame while paused", () => {
    expect(shouldCorrectDrift({ target: 10, current: 10.15, paused: false })).toBe(false);
    expect(shouldCorrectDrift({ target: 10, current: 10.25, paused: false })).toBe(true);
    expect(shouldCorrectDrift({ target: 10, current: 9.75, paused: false })).toBe(true);
    expect(shouldCorrectDrift({ target: 10, current: 10.01, paused: true })).toBe(false);
    expect(shouldCorrectDrift({ target: 10, current: 10.05, paused: true })).toBe(true);
  });
});

describe("insetStream", () => {
  it("plays the rendition when there is one, whatever the full-resolution preference", () => {
    expect(insetStream({ trim_version: "t1", scrub_version: "w1" })).toEqual({ kind: "scrub", version: "w1" });
    expect(insetStream({ kind: "trim", trim_version: "t1", scrub_version: "w1" })).toEqual({
      kind: "scrub",
      version: "w1",
    });
    expect(insetStream({ kind: "web" })).toEqual({ kind: "web", version: null });
  });

  it("falls back to the trim without a rendition or after it failed", () => {
    expect(insetStream({ trim_version: "t1", scrub_version: null })).toEqual({ kind: "trim", version: "t1" });
    expect(insetStream({ trim_version: "t1", scrub_version: "w1" }, true)).toEqual({ kind: "trim", version: "t1" });
    expect(insetStream({ kind: "web", trim_version: "t1" }, true)).toEqual({ kind: "trim", version: "t1" });
  });

  it("keeps a source camera on the source: its beep anchor is in the source", () => {
    expect(insetStream({ kind: "source", scrub_version: "w1" })).toEqual({ kind: "source", version: null });
  });
});
