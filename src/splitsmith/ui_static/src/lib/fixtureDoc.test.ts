import { describe, expect, it } from "vitest";

import { deriveMarkers } from "./audit-doc";
import { buildFixtureJson } from "./fixtureDoc";

const base = {
  beep_time: 0,
  shots: [
    {
      shot_number: 1,
      candidate_number: 5,
      time: 2.268,
      ms_after_beep: 2268,
      source: "promoted",
      subclass: "steel",
      snap_displacement_ms: 57.41,
      sanity_flag: "",
    },
    { shot_number: 2, candidate_number: 5, time: 2.5, ms_after_beep: 2500, source: "promoted", subclass: "paper" },
    { shot_number: 3, candidate_number: null, time: 3.1, ms_after_beep: 3100, source: "manual", subclass: "barrel" },
  ],
  _candidates_pending_audit: {
    candidates: [
      { candidate_number: 5, time: 2.193 },
      { candidate_number: 6, time: 2.9 },
    ],
  },
  audit_events: [],
};

describe("buildFixtureJson", () => {
  it("round-trips a fixture untouched: every shot, time and field", () => {
    const saved = buildFixtureJson({ base: base as never, markers: deriveMarkers(base as never), appendEvents: [] });
    expect(saved.shots).toEqual(base.shots);
  });

  it("keeps a moved shot's labels, drops its snap displacement and stops calling it promoted", () => {
    const markers = deriveMarkers(base as never).map((m) => (m.id === "cand-5" ? { ...m, time: 2.21 } : m));
    const [first] = buildFixtureJson({ base: base as never, markers, appendEvents: [] }).shots as unknown as Array<Record<string, unknown>>;
    expect(first).toMatchObject({ time: 2.21, ms_after_beep: 2210, subclass: "steel", source: "detected" });
    expect(first).not.toHaveProperty("snap_displacement_ms");
  });

  it("writes a candidate made a shot, and a new manual shot, with only what the page knows", () => {
    const markers = deriveMarkers(base as never).map((m) => (m.id === "cand-6" ? { ...m, kind: "detected" as const } : m));
    markers.push({ id: "manual-new", kind: "manual", time: 4, candidateNumber: null, confidence: null, peakAmplitude: null, note: "" });
    const shots = buildFixtureJson({ base: base as never, markers, appendEvents: [] }).shots;
    expect(shots.map((s) => [s.time, s.candidate_number, s.source])).toEqual([
      [2.268, 5, "promoted"],
      [2.5, 5, "promoted"],
      [2.9, 6, "detected"],
      [3.1, null, "manual"],
      [4, null, "manual"],
    ]);
  });
});
