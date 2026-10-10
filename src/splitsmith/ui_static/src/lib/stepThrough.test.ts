import { describe, expect, it } from "vitest";

import type { DevReviewQueueItem } from "@/lib/api";
import { closeupBins, nextFixtureToReview, stepActionForKey, stepQueue } from "@/lib/stepThrough";

/** 1 ms bins over 1 s: silence with a clean shot whose rise starts at ``foot``. */
function peaksWithShotAt(foot: number, duration = 1): { peaks: number[]; duration: number } {
  const n = Math.round(duration * 1000);
  const peaks = new Array<number>(n).fill(0.001);
  const start = Math.round(foot * 1000);
  peaks[start] = 0.3;
  peaks[start + 1] = 0.8;
  peaks[start + 2] = 1.0;
  for (let i = 3; i < 30; i++) peaks[start + i] = 1.0 - i * 0.03;
  return { peaks, duration };
}

describe("stepQueue", () => {
  it("lists a shot whose rise foot is more than 5 ms from its stored time", () => {
    const peaks = peaksWithShotAt(0.5);
    const q = stepQueue([{ id: "a", time: 0.512 }], peaks);
    expect(q).toEqual([{ id: "a", stored: 0.512, suggested: 0.5, moveMs: -12 }]);
  });

  it("leaves a shot within 5 ms of its rise foot out", () => {
    const peaks = peaksWithShotAt(0.5);
    expect(stepQueue([{ id: "a", time: 0.504 }], peaks)).toEqual([]);
  });

  it("leaves a shot with no rise foot in reach out", () => {
    const peaks = peaksWithShotAt(0.5);
    expect(stepQueue([{ id: "a", time: 0.9 }], peaks)).toEqual([]);
  });

  it("keeps time order", () => {
    const a = peaksWithShotAt(0.2).peaks;
    const b = peaksWithShotAt(0.6).peaks;
    const peaks = { peaks: a.map((v, i) => Math.max(v, b[i])), duration: 1 };
    const q = stepQueue(
      [
        { id: "late", time: 0.61 },
        { id: "early", time: 0.21 },
      ],
      peaks,
    );
    expect(q.map((x) => x.id)).toEqual(["early", "late"]);
  });
});

describe("stepActionForKey", () => {
  const key = (k: string, mods: Partial<KeyboardEvent> = {}) =>
    stepActionForKey({ key: k, shiftKey: false, metaKey: false, ctrlKey: false, altKey: false, ...mods });

  it("maps the step keys", () => {
    expect(key("Enter")).toEqual({ kind: "accept" });
    expect(key(" ")).toEqual({ kind: "keep" });
    expect(key("x")).toEqual({ kind: "reject" });
    expect(key("Backspace")).toEqual({ kind: "back" });
    expect(key("ArrowLeft")).toEqual({ kind: "nudge", ms: -1 });
    expect(key("ArrowRight", { shiftKey: true })).toEqual({ kind: "nudge", ms: 5 });
  });

  it("leaves modified keys and everything else to the page", () => {
    expect(key("Enter", { metaKey: true })).toBeNull();
    expect(key("ArrowLeft", { altKey: true })).toBeNull();
    expect(key("m")).toBeNull();
  });
});

describe("closeupBins", () => {
  it("returns the 1 ms bins of a window around a time, with their start times", () => {
    const peaks = peaksWithShotAt(0.5);
    const bins = closeupBins(peaks, 0.5, 0.004);
    expect(bins.map((b) => Math.round(b.t * 1000))).toEqual([496, 497, 498, 499, 500, 501, 502, 503]);
    expect(bins[4].v).toBe(0.3);
  });

  it("clips the window at the clip's ends", () => {
    const peaks = peaksWithShotAt(0.5);
    expect(closeupBins(peaks, 0.001, 0.004).map((b) => Math.round(b.t * 1000))).toEqual([0, 1, 2, 3, 4]);
  });
});

describe("nextFixtureToReview", () => {
  const item = (slug: string, review_status: "needs_review" | "reviewed") =>
    ({ slug, audit_path: `/fx/${slug}.json`, review_status }) as DevReviewQueueItem;

  it("takes the first pending fixture whose times need checking, never the current one", () => {
    const pending = [item("cur", "needs_review"), item("label-only", "reviewed"), item("next", "needs_review")];
    expect(nextFixtureToReview(pending, "cur")?.slug).toBe("next");
  });

  it("is null when nothing is left", () => {
    expect(nextFixtureToReview([item("cur", "needs_review")], "cur")).toBeNull();
  });
});
