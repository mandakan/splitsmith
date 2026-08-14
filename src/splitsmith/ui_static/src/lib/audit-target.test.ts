import { describe, expect, it } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { TARGET_BAND_S, resolveTarget } from "@/lib/audit-target";

function marker(over: Partial<AuditMarker>): AuditMarker {
  return {
    id: "cand-1",
    kind: "detected",
    time: 1.0,
    candidateNumber: 1,
    confidence: 0.9,
    peakAmplitude: null,
    note: "",
    ...over,
  };
}

describe("resolveTarget", () => {
  it("returns none when nothing is inside the band", () => {
    const t = resolveTarget([marker({ time: 5.0 })], 1.0);
    expect(t.kind).toBe("none");
  });

  it("a kept shot inside the band is the target", () => {
    const m = marker({ time: 1.05 });
    const t = resolveTarget([m], 1.0);
    expect(t).toEqual({ kind: "shot", marker: m });
  });

  it("a kept shot beats a nearer rejected candidate", () => {
    const kept = marker({ id: "cand-1", time: 1.1 });
    const rej = marker({ id: "cand-2", kind: "rejected", time: 1.01, candidateNumber: 2 });
    const t = resolveTarget([rej, kept], 1.0);
    expect(t).toEqual({ kind: "shot", marker: kept });
  });

  it("a rejected candidate beats nothing", () => {
    const rej = marker({ id: "cand-2", kind: "rejected", time: 1.05, candidateNumber: 2 });
    const t = resolveTarget([rej], 1.0);
    expect(t).toEqual({ kind: "candidate", marker: rej });
  });

  it("the nearest of two kept shots wins", () => {
    const near = marker({ id: "cand-1", time: 1.02 });
    const far = marker({ id: "cand-2", time: 1.09, candidateNumber: 2 });
    expect(resolveTarget([far, near], 1.0)).toEqual({ kind: "shot", marker: near });
  });

  it("manual markers count as kept shots", () => {
    const m = marker({ id: "manual-1", kind: "manual", candidateNumber: null });
    expect(resolveTarget([m], 1.0).kind).toBe("shot");
  });

  it("the band is fixed in time: exactly TARGET_BAND_S away is in, a hair past is out", () => {
    const edge = marker({ time: 1.0 + TARGET_BAND_S });
    expect(resolveTarget([edge], 1.0).kind).toBe("shot");
    const past = marker({ time: 1.0 + TARGET_BAND_S + 0.001 });
    expect(resolveTarget([past], 1.0).kind).toBe("none");
  });

  it("a held id stays the target even after the marker walks out of the band", () => {
    const nudged = marker({ id: "cand-1", time: 1.5 });
    const t = resolveTarget([nudged], 1.0, "cand-1");
    expect(t).toEqual({ kind: "shot", marker: nudged });
  });

  it("a held id that no longer exists falls back to the band rule", () => {
    const m = marker({ time: 1.05 });
    expect(resolveTarget([m], 1.0, "gone")).toEqual({ kind: "shot", marker: m });
  });
});
