import { describe, expect, it } from "vitest";

import type { PendingUpload } from "@/lib/uploads";
import {
  queueStats,
  summaryLine,
  summaryParts,
  trimSamples,
  type QueueStats,
  type ThroughputSample,
} from "@/lib/uploadStats";

/** A PendingUpload with only the fields queueStats reads. */
function upload(
  status: PendingUpload["status"],
  size: number,
  bytesSent = status === "done" ? size : 0,
): PendingUpload {
  return {
    id: `${status}-${size}-${bytesSent}-${Math.random()}`,
    file: { name: "clip.mp4", size } as File,
    slug: "me",
    stages: [],
    status,
    bytesSent,
  };
}

describe("queueStats -- counts", () => {
  it("numbers the active file among the files that will still be attempted", () => {
    // 1 done, 1 cancelled, then the active one. The cancelled file will
    // never upload, so the active file is the 2nd of 3 attempts -- not
    // the 3rd of 4, and not the 2nd of 4.
    const stats = queueStats(
      [
        upload("done", 100),
        upload("cancelled", 100),
        upload("uploading", 100, 50),
        upload("queued", 100),
      ],
      [],
      0,
    );

    expect(stats.activeIndex).toBe(2);
    expect(stats.countable).toBe(3);
  });

  it("reports no active file when nothing is uploading", () => {
    const stats = queueStats([upload("done", 100), upload("queued", 100)], [], 0);

    expect(stats.activeIndex).toBeNull();
  });

  it("counts errors as failures but not cancellations", () => {
    const stats = queueStats(
      [upload("error", 100), upload("error", 100), upload("cancelled", 100)],
      [],
      0,
    );

    expect(stats.failedCount).toBe(2);
  });
});

describe("queueStats -- percentage", () => {
  it("reaches 100 percent once every file that can succeed has", () => {
    // The failed and cancelled files' bytes must leave the denominator,
    // or a queue containing either can never read as finished.
    const stats = queueStats(
      [upload("done", 100), upload("error", 100, 30), upload("cancelled", 100, 10)],
      [],
      0,
    );

    expect(stats.pct).toBe(100);
  });

  it("measures progress in bytes, not in files", () => {
    // One 900-byte file done, one 100-byte file untouched: 90%, not 50%.
    const stats = queueStats([upload("done", 900), upload("queued", 100)], [], 0);

    expect(stats.pct).toBe(90);
  });

  it("does not divide by zero on a queue of empty files", () => {
    const stats = queueStats([upload("queued", 0), upload("queued", 0)], [], 0);

    expect(stats.pct).toBe(0);
    expect(stats.bytesTotal).toBe(0);
  });

  it("excludes dead files from the byte totals", () => {
    const stats = queueStats(
      [upload("uploading", 100, 25), upload("cancelled", 500, 400), upload("queued", 100)],
      [],
      0,
    );

    expect(stats.bytesTotal).toBe(200);
    expect(stats.bytesSent).toBe(25);
  });
});

const window = (samples: [number, number][]): ThroughputSample[] =>
  samples.map(([t, bytes]) => ({ t, bytes }));

describe("summaryLine", () => {
  const stats = (patch: Partial<QueueStats> = {}): QueueStats => ({
    activeIndex: 3,
    countable: 12,
    doneCount: 2,
    failedCount: 0,
    bytesSent: 4_000_000,
    bytesTotal: 10_000_000,
    bytesRemaining: 6_000_000,
    pct: 41,
    etaSeconds: 380,
    ...patch,
  });

  it("names the active file, the progress and the time left", () => {
    expect(summaryLine(stats(), true)).toBe("Uploading 3 of 12 . 41% . 5.7 MB left . ~6 min");
  });

  it("switches to a finished count once the queue drains", () => {
    // "Uploading 3 of 12" on an idle queue would read as still running.
    expect(summaryLine(stats({ activeIndex: null, doneCount: 12, pct: 100 }), false)).toBe(
      "Uploads 12/12 . 100%",
    );
  });

  it("drops the eta when there is none to show", () => {
    expect(summaryLine(stats({ etaSeconds: null }), true)).toBe(
      "Uploading 3 of 12 . 41% . 5.7 MB left",
    );
  });

  it("reports failures rather than letting them hide in the percentage", () => {
    expect(summaryLine(stats({ failedCount: 2 }), true)).toContain("2 failed");
  });

  it("appends a scope note when given one", () => {
    expect(summaryLine(stats(), true, "all shooters")).toContain(". all shooters");
  });

  it("omits the bytes-left clause with nothing left to send", () => {
    expect(summaryLine(stats({ bytesRemaining: 0, etaSeconds: null }), true)).toBe(
      "Uploading 3 of 12 . 41%",
    );
  });

  describe("summaryParts", () => {
    it("keeps the count and percentage on the headline", () => {
      // The dock is 360px wide and the full line wraps there. Splitting
      // it deliberately beats letting it wrap mid-clause and change the
      // dock's height as the ETA appears and disappears.
      expect(summaryParts(stats(), true).primary).toBe("Uploading 3 of 12 . 41%");
    });

    it("puts the volatile clauses in the detail", () => {
      expect(summaryParts(stats({ failedCount: 1 }), true).detail).toBe(
        "5.7 MB left . ~6 min . 1 failed",
      );
    });

    it("has no detail when there is nothing beyond the headline", () => {
      expect(
        summaryParts(stats({ activeIndex: null, bytesRemaining: 0, etaSeconds: null }), false)
          .detail,
      ).toBeNull();
    });

    it("joins to exactly the single-line form", () => {
      // Two renderings of one truth: if these drift, the dock and the
      // modal start telling different stories.
      const q = stats({ failedCount: 1 });
      const { primary, detail } = summaryParts(q, true, "all shooters");
      expect([primary, detail].filter(Boolean).join(" . ")).toBe(summaryLine(q, true, "all shooters"));
    });
  });
});

describe("trimSamples", () => {
  it("keeps one sample from before the cutoff so the window has a left edge", () => {
    // Dropping everything older than the cutoff would leave a window
    // starting at the cutoff itself, throwing away the observation the
    // rate is measured against.
    const trimmed = trimSamples(
      window([
        [0, 0],
        [3000, 300],
        [6000, 600],
      ]),
      10_000,
      5000,
    );

    expect(trimmed.map((s) => s.t)).toEqual([3000, 6000]);
  });

  it("leaves a window that is already inside the cutoff alone", () => {
    const samples = window([
      [8000, 100],
      [9000, 200],
    ]);

    expect(trimSamples(samples, 10_000, 5000)).toEqual(samples);
  });

  it("keeps the last sample when every sample is older than the cutoff", () => {
    // A stalled upload stops producing progress ticks. Emptying the
    // window here would erase the evidence that it stalled.
    const trimmed = trimSamples(
      window([
        [0, 0],
        [1000, 100],
      ]),
      10_000,
      5000,
    );

    expect(trimmed.map((s) => s.t)).toEqual([1000]);
  });
});

describe("queueStats -- eta", () => {

  it("projects the remaining bytes at the observed rate", () => {
    // 1000 bytes over 4s = 250 B/s. 400 of 1000 bytes sent, so 600 left
    // -> 2.4s.
    const stats = queueStats(
      [upload("uploading", 1000, 400)],
      window([
        [1000, 0],
        [5000, 1000],
      ]),
      5000,
    );

    expect(stats.etaSeconds).toBeCloseTo(2.4, 5);
  });

  it("gives no eta from a single sample", () => {
    const stats = queueStats([upload("uploading", 1000, 400)], window([[1000, 400]]), 1000);

    expect(stats.etaSeconds).toBeNull();
  });

  it("gives no eta until the window is wide enough to be meaningful", () => {
    // Half a second of observation would let one slow chunk project an
    // absurd number of minutes.
    const stats = queueStats(
      [upload("uploading", 1000, 400)],
      window([
        [1000, 380],
        [1500, 400],
      ]),
      1500,
    );

    expect(stats.etaSeconds).toBeNull();
  });

  it("gives no eta when the window shows no bytes moving", () => {
    const stats = queueStats(
      [upload("uploading", 1000, 400)],
      window([
        [1000, 400],
        [9000, 400],
      ]),
      9000,
    );

    expect(stats.etaSeconds).toBeNull();
  });

  it("gives no eta once the newest reading has gone stale", () => {
    // A stalled upload stops producing progress events, so the window
    // stops being re-trimmed and keeps a rate that was true 20 seconds
    // ago. Counting down from it would show a confident estimate for an
    // upload that has stopped moving.
    const samples = window([
      [1000, 0],
      [5000, 1000],
    ]);

    expect(queueStats([upload("uploading", 2000, 1000)], samples, 5000).etaSeconds).not.toBeNull();
    expect(queueStats([upload("uploading", 2000, 1000)], samples, 25_000).etaSeconds).toBeNull();
  });

  it("gives no eta when nothing is left to send", () => {
    const stats = queueStats(
      [upload("done", 1000)],
      window([
        [1000, 0],
        [5000, 1000],
      ]),
      5000,
    );

    expect(stats.etaSeconds).toBeNull();
  });
});
