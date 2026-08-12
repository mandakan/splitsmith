import { describe, expect, it } from "vitest";

import { buildAuditJson, deriveMarkers } from "./audit-doc";

const stage = { stage_number: 1, stage_name: "B5", time_seconds: 29.49 };

describe("shot id round-trip", () => {
  it("reads a persisted id rather than rebuilding a positional one", () => {
    const markers = deriveMarkers({
      shots: [
        { shot_number: 1, candidate_number: null, time: 7.181, id: "manual-abc123", source: "manual" },
      ],
      _candidates_pending_audit: { candidates: [] },
    } as never);
    const manual = markers.find((m) => m.kind === "manual");
    expect(manual?.shotId).toBe("manual-abc123");
    expect(manual?.id).toBe("manual-abc123");
  });

  it("falls back to the positional id on a legacy doc with no ids", () => {
    const markers = deriveMarkers({
      shots: [{ shot_number: 2, candidate_number: null, time: 7.181, source: "manual" }],
      _candidates_pending_audit: { candidates: [] },
    } as never);
    expect(markers.find((m) => m.kind === "manual")?.id).toBe("manual-shot-2");
  });

  it("writes the id back out so a nudge stays a move", () => {
    const doc = buildAuditJson({
      base: null,
      stage,
      primaryBeepInClip: 5,
      markers: [
        {
          id: "manual-abc123",
          shotId: "manual-abc123",
          kind: "manual",
          time: 7.2,
          candidateNumber: null,
          confidence: null,
          peakAmplitude: null,
          note: "",
        },
      ],
      appendEvents: [],
    } as never);
    expect(doc.shots[0].id).toBe("manual-abc123");
  });

  it("omits the id for detected shots -- the server derives cand-<n>", () => {
    const doc = buildAuditJson({
      base: null,
      stage,
      primaryBeepInClip: 5,
      markers: [
        {
          id: "cand-37",
          shotId: null,
          kind: "detected",
          time: 7.2,
          candidateNumber: 37,
          confidence: 0.8,
          peakAmplitude: 0.5,
          note: "",
        },
      ],
      appendEvents: [],
    } as never);
    expect(doc.shots[0].id).toBeUndefined();
    expect(doc.shots[0].candidate_number).toBe(37);
  });
});
