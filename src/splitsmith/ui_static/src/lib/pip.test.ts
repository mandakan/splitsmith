import { describe, expect, it } from "vitest";

import {
  containedFrame,
  cornerByArrow,
  cyclePip,
  insetPlan,
  PIP_END_GUARD_S,
  servedClipBeep,
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

  it("opens a named start camera big, the primary first in the inset", () => {
    expect(initialPip(cams(3), "c3")).toEqual({ big: "c3", inset: "c1" });
  });

  it("ignores a start camera that is missing or cannot be lined up", () => {
    expect(initialPip(cams(2), "c9")).toEqual({ big: "c1", inset: "c2" });
    expect(initialPip([cam(1), cam(2, { beepInClip: null })], "c2")).toEqual({ big: "c1", inset: null });
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

  it("starts over on the start camera when there is one", () => {
    expect(normalizePip({ big: "", inset: null }, cams(2), "c2")).toEqual({ big: "c2", inset: "c1" });
  });

  it("drops the inset when the big camera lost its beep", () => {
    const list = [cam(1, { beepInClip: null }), cam(2)];
    expect(normalizePip({ big: "c1", inset: "c2" }, list)).toEqual({ big: "c1", inset: null });
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

  it("ignores auto-repeat and typing in a field", () => {
    expect(pipKeyAction({ key: "c", repeat: true })).toBeNull();
    for (const tag of ["input", "textarea", "select"]) {
      expect(pipKeyAction({ key: "c", target: document.createElement(tag) })).toBeNull();
    }
    const editable = document.createElement("div");
    editable.contentEditable = "true";
    // jsdom does not derive isContentEditable from the attribute.
    Object.defineProperty(editable, "isContentEditable", { value: true });
    expect(pipKeyAction({ key: "c", target: editable })).toBeNull();
    expect(pipKeyAction({ key: "c", target: document.createElement("button") })).toBe(1);
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

  it("clamps to the inset clip's start, and to just before its end", () => {
    expect(insetTime({ bigTime: 1, bigBeep: 5, insetBeep: 2 })).toBe(0);
    expect(insetTime({ bigTime: 40, bigBeep: 5, insetBeep: 5, insetDuration: 30 })).toBeCloseTo(30 - PIP_END_GUARD_S);
    expect(insetTime({ bigTime: 40, bigBeep: 5, insetBeep: 5, insetDuration: NaN })).toBe(40);
  });

  it("corrects drift past 0.2 s while playing, past 15 ms while held", () => {
    expect(shouldCorrectDrift({ target: 10, current: 10.15, playing: true })).toBe(false);
    expect(shouldCorrectDrift({ target: 10, current: 10.25, playing: true })).toBe(true);
    expect(shouldCorrectDrift({ target: 10, current: 9.75, playing: true })).toBe(true);
    expect(shouldCorrectDrift({ target: 10, current: 10.01, playing: false })).toBe(false);
    expect(shouldCorrectDrift({ target: 10, current: 10.02, playing: false })).toBe(true);
    expect(shouldCorrectDrift({ target: 10, current: 10.05, playing: false })).toBe(true);
  });
});

describe("insetPlan", () => {
  const base = { bigBeep: 0, insetBeep: 0, insetDuration: 4, bigPaused: false };

  it("plays in range while the big plays", () => {
    expect(insetPlan({ ...base, bigTime: 2 })).toEqual({ target: 2, inRange: true, play: true });
  });

  it("holds paused at the start while the big is before the inset clip", () => {
    expect(insetPlan({ ...base, bigBeep: 3, bigTime: 1 })).toEqual({ target: 0, inRange: false, play: false });
  });

  it("holds paused just before the end once the big passes the inset clip", () => {
    const p = insetPlan({ ...base, bigTime: 5 });
    expect(p.inRange).toBe(false);
    expect(p.play).toBe(false);
    expect(p.target).toBeCloseTo(4 - PIP_END_GUARD_S);
    expect(insetPlan({ ...base, bigTime: 3.95 }).play).toBe(false);
  });

  it("holds while the big is paused or stalled", () => {
    expect(insetPlan({ ...base, bigTime: 2, bigPaused: true }).play).toBe(false);
    expect(insetPlan({ ...base, bigTime: 2, bigStalled: true }).play).toBe(false);
  });

  it("an unknown duration is open-ended", () => {
    expect(insetPlan({ ...base, insetDuration: NaN, bigTime: 99 }).play).toBe(true);
  });
});

describe("servedClipBeep", () => {
  it("maps Audit's served-clip offset onto the audit beep", () => {
    expect(servedClipBeep({ index: 0, offset: 0, auditBeep: 5, beepTime: 12 })).toBe(5);
    expect(servedClipBeep({ index: 1, offset: -2, auditBeep: 5, beepTime: 3 })).toBe(3);
  });

  it("is unsyncable without the audit beep or a secondary's own beep", () => {
    expect(servedClipBeep({ index: 0, offset: 0, auditBeep: null, beepTime: 12 })).toBeNull();
    expect(servedClipBeep({ index: 1, offset: 0, auditBeep: 5, beepTime: null })).toBeNull();
  });
});

describe("insetStream", () => {
  const none = new Set<never>();

  it("plays the rendition when there is one, whatever the full-resolution preference", () => {
    expect(insetStream({ trim_version: "t1", scrub_version: "w1" })).toEqual({ kind: "scrub", version: "w1" });
    expect(insetStream({ kind: "trim", trim_version: "t1", scrub_version: "w1" }, none)).toEqual({
      kind: "scrub",
      version: "w1",
    });
    expect(insetStream({ kind: "web" })).toEqual({ kind: "web", version: null });
  });

  it("falls back to the trim without a rendition or after it failed", () => {
    expect(insetStream({ trim_version: "t1", scrub_version: null })).toEqual({ kind: "trim", version: "t1" });
    expect(insetStream({ trim_version: "t1", scrub_version: "w1" }, new Set(["scrub"]))).toEqual({
      kind: "trim",
      version: "t1",
    });
    expect(insetStream({ kind: "web", trim_version: "t1" }, new Set(["web"]))).toEqual({ kind: "trim", version: "t1" });
  });

  it("keeps a source or proxy camera on that clip: its beep anchor is in it", () => {
    expect(insetStream({ kind: "source", scrub_version: "w1" })).toEqual({ kind: "source", version: null });
    expect(insetStream({ kind: "proxy", trim_version: "t1", scrub_version: "w1" })).toEqual({
      kind: "proxy",
      version: null,
    });
  });

  it("answers null once every kind failed, so the page can mark the camera unavailable", () => {
    expect(insetStream({ kind: "proxy" }, new Set(["proxy"]))).toBeNull();
    expect(insetStream({ kind: "source" }, new Set(["source"]))).toBeNull();
    expect(insetStream({ scrub_version: "w1" }, new Set(["scrub", "trim"]))).toBeNull();
    expect(insetStream({ kind: "web" }, new Set(["web", "trim"]))).toBeNull();
  });
});

describe("cornerByArrow", () => {
  it("moves to the arrow's side, keeping the other axis", () => {
    expect(cornerByArrow("tr", "ArrowDown")).toBe("br");
    expect(cornerByArrow("tr", "ArrowLeft")).toBe("tl");
    expect(cornerByArrow("bl", "ArrowUp")).toBe("tl");
    expect(cornerByArrow("bl", "ArrowRight")).toBe("br");
  });

  it("is null on the side it is already on, or for another key", () => {
    expect(cornerByArrow("tr", "ArrowUp")).toBeNull();
    expect(cornerByArrow("tr", "ArrowRight")).toBeNull();
    expect(cornerByArrow("tr", "Enter")).toBeNull();
  });
});
