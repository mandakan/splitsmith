import { describe, expect, it } from "vitest";

import type { DevReviewQueueItem, FixtureReviewSignals, StageAudit } from "@/lib/api";
import {
  fixtureAuditHref,
  fixtureProject,
  fixtureVideoOffset,
  nextInQueue,
  queueFilterMatches,
  signalAnomalies,
  signOffLine,
} from "@/lib/fixtureReview";

const signals = (over: Partial<FixtureReviewSignals> = {}): FixtureReviewSignals => ({
  moves: [],
  edges: [],
  unmarked: [],
  echoes: [],
  reference: null,
  rounds: null,
  ...over,
});

describe("signalAnomalies", () => {
  it("turns each signal into a flag at its time, worded for the shot list", () => {
    const flags = signalAnomalies(
      signals({
        moves: [{ time: 6.0, suggested: 5.992, move_ms: -8 }],
        edges: [{ time: 6.5, displacement_ms: -58 }],
        unmarked: [{ time: 8.4, confidence: 0.7 }],
      }),
    );
    expect(flags.map((f) => [f.kind, f.time])).toEqual([
      ["review_move", 6.0],
      ["review_edge", 6.5],
      ["review_unmarked", 8.4],
    ]);
    expect(flags[0].message).toMatch(/8 ms earlier/);
    expect(flags[1].message).toMatch(/edge of its snap window/);
    expect(flags[2].message).toMatch(/no shot/);
    expect(flags.every((f) => f.severity === "warn")).toBe(true);
  });
});

describe("echo flags", () => {
  it("names the blast before the shot and the fix", () => {
    const [f] = signalAnomalies(signals({ echoes: [{ time: 2.025, earlier: 2.0 }] }));
    expect([f.kind, f.time]).toEqual(["review_echo", 2.025]);
    expect(f.message).toBe("On an echo? A blast at 2.000 s, 25 ms before: Earlier rise moves it there");
  });
});

describe("signOffLine", () => {
  const ref = { kind: "snap" as const, times: [6.0, 6.5, 7.0] };

  it("matches the shots it was snapped with, moved shots included", () => {
    const line = signOffLine([6.004, 6.49, 7.0], signals({ reference: ref, rounds: 2 }));
    expect(line.ok).toBe(true);
    expect(line.text).toBe("3 shots, as snapped from the primary. The scorecard says 2 rounds.");
  });

  it("names a shot added or removed against the snap, never the rounds", () => {
    const added = signOffLine([6.0, 6.5, 7.0, 9.25], signals({ reference: ref }));
    expect(added.ok).toBe(false);
    expect(added.text).toBe("4 shots against 3 snapped from the primary: added at 9.25 s.");
    const removed = signOffLine([6.0, 7.0], signals({ reference: ref }));
    expect(removed.text).toBe("2 shots against 3 snapped from the primary: removed at 6.50 s.");
  });

  it("without a snap, counts the shots and mentions the rounds only as a note", () => {
    expect(signOffLine([1, 2], signals({ rounds: 3 }))).toEqual({
      ok: true,
      text: "2 shots. The scorecard says 3 rounds.",
    });
    expect(signOffLine([1, 2], null)).toEqual({ ok: true, text: "2 shots." });
  });
});

describe("fixtureProject", () => {
  const doc = {
    stage_number: 10,
    stage_name: "B5",
    stage_time_seconds: 28.6,
    beep_time: 5.0,
    fixture_window_in_source: [1.5, 40],
    shots: [],
  } as unknown as StageAudit;

  it("is one stage whose primary is the fixture's source, its beep reviewed", () => {
    const p = fixtureProject(doc, "/raw/VID_1.mp4");
    expect(p.stages).toHaveLength(1);
    const s = p.stages[0];
    expect([s.stage_number, s.stage_name, s.time_seconds]).toEqual([10, "B5", 28.6]);
    const v = s.videos[0];
    expect([v.role, v.path, v.beep_reviewed]).toEqual(["primary", "/raw/VID_1.mp4", true]);
    // The source's beep: the fixture's clip starts 1.5 s into the source.
    expect(v.beep_time).toBeCloseTo(6.5, 6);
  });

  it("puts the video's clock the window's start ahead of the fixture's", () => {
    expect(fixtureVideoOffset(doc)).toBe(1.5);
    expect(fixtureVideoOffset({ shots: [] } as unknown as StageAudit)).toBe(0);
  });
});

describe("the review queue's filter and next fixture", () => {
  const item = (slug: string, status: "needs_review" | "reviewed" = "needs_review") =>
    ({ slug, review_status: status, venue: null, source_label: "match" }) as DevReviewQueueItem;
  const queue = [
    item("stage-shots-a-stage3-go3s"),
    item("stage-shots-hfo-masters-2026-stage1-go3s"),
    item("stage-shots-b-stage1-iphone"),
    item("stage-shots-hfo-masters-2026-stage2-go3s"),
    item("stage-shots-hfo-masters-2026-stage4-go3s", "reviewed"),
    item("stage-shots-hfo-masters-2026-stage5-go3s"),
  ];

  it("matches every word of the filter, in any order and case", () => {
    expect(queueFilterMatches(queue[1], "GO3S hfo")).toBe(true);
    expect(queueFilterMatches(queue[2], "hfo go3s")).toBe(false);
    expect(queueFilterMatches(queue[2], "  ")).toBe(true);
  });

  it("takes the next fixture after this one, not the top of the queue", () => {
    expect(nextInQueue(queue, "stage-shots-b-stage1-iphone", "")?.slug).toBe(
      "stage-shots-hfo-masters-2026-stage2-go3s",
    );
  });

  it("stays inside the filter and skips reviewed fixtures", () => {
    expect(nextInQueue(queue, "stage-shots-hfo-masters-2026-stage2-go3s", "hfo go3s")?.slug).toBe(
      "stage-shots-hfo-masters-2026-stage5-go3s",
    );
  });

  it("wraps to the filter's first open fixture, and is null when none is left", () => {
    expect(nextInQueue(queue, "stage-shots-hfo-masters-2026-stage5-go3s", "hfo")?.slug).toBe(
      "stage-shots-hfo-masters-2026-stage1-go3s",
    );
    expect(nextInQueue([queue[1]], queue[1].slug, "hfo")).toBeNull();
  });

  it("carries the filter on the Audit link", () => {
    expect(fixtureAuditHref("/f/a.json", null, "hfo go3s")).toBe(
      "/review/audit?fixture=%2Ff%2Fa.json&q=hfo%20go3s",
    );
    expect(fixtureAuditHref("/f/a.json", null, "")).toBe("/review/audit?fixture=%2Ff%2Fa.json");
  });
});
