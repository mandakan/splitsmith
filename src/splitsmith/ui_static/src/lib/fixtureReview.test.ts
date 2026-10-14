import { describe, expect, it } from "vitest";

import type { FixtureReviewSignals, StageAudit } from "@/lib/api";
import { fixtureProject, fixtureVideoOffset, signalAnomalies, signOffLine } from "@/lib/fixtureReview";

const signals = (over: Partial<FixtureReviewSignals> = {}): FixtureReviewSignals => ({
  moves: [],
  edges: [],
  unmarked: [],
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
