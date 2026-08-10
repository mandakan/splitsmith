import { describe, expect, it } from "vitest";

import { planServedClip } from "./camPlayback";

/**
 * Numbers mirror a real project (hfo-masters-2026 stage 1): primary beep
 * 25.130s in source, secondary beep 5.951s, trim pre-buffer 5s. Both
 * per-role trims are cut around their own beep, so in trimmed space both
 * beeps sit at min(beep, pre_buffer) - see ui/audio.py (beep_in_clip).
 */
const PRE = 5.0;

describe("planServedClip", () => {
  it("primary: served clip IS the audit timeline, offset 0, trim when trimmed", () => {
    expect(
      planServedClip({
        index: 0,
        beepTime: 25.13,
        processedTrim: true,
        primaryPeaksTrimmed: true,
        auditBeep: 5.0,
        preBufferSeconds: PRE,
      }),
    ).toEqual({ kind: "trim", offset: 0 });
  });

  it("primary without a trim streams the proxy at offset 0", () => {
    expect(
      planServedClip({
        index: 0,
        beepTime: 25.13,
        processedTrim: false,
        primaryPeaksTrimmed: false,
        auditBeep: 25.13,
        preBufferSeconds: PRE,
      }),
    ).toEqual({ kind: "proxy", offset: 0 });
  });

  it("trimmed secondary maps via clip-local beeps, not source beeps", () => {
    // Old bug: offset was beep_time - auditBeep = 5.951 - 5.0 in trimmed
    // space only by luck of a beep near the buffer; for a secondary whose
    // source beep sits at 60s the source-space offset seeks ~55s into a
    // ~24s trim -> black frame. Correct trimmed-space offset here is 0.
    expect(
      planServedClip({
        index: 1,
        beepTime: 5.951,
        processedTrim: true,
        primaryPeaksTrimmed: true,
        auditBeep: 5.0,
        preBufferSeconds: PRE,
      }),
    ).toEqual({ kind: "trim", offset: 0 });

    const far = planServedClip({
      index: 1,
      beepTime: 60.0,
      processedTrim: true,
      primaryPeaksTrimmed: true,
      auditBeep: 5.0,
      preBufferSeconds: PRE,
    });
    expect(far).toEqual({ kind: "trim", offset: 0 });
  });

  it("trimmed secondary whose beep is inside the pre-buffer starts short", () => {
    // beep at 2s < 5s pre-buffer: the trim starts at source 0, so the
    // clip-local beep is 2.0 and the offset vs a 5.0 audit beep is -3.0.
    expect(
      planServedClip({
        index: 1,
        beepTime: 2.0,
        processedTrim: true,
        primaryPeaksTrimmed: true,
        auditBeep: 5.0,
        preBufferSeconds: PRE,
      }),
    ).toEqual({ kind: "trim", offset: -3.0 });
  });

  it("untrimmed secondary falls back to source-space mapping via proxy", () => {
    const plan = planServedClip({
      index: 1,
      beepTime: 5.951,
      processedTrim: false,
      primaryPeaksTrimmed: true,
      auditBeep: 5.0,
      preBufferSeconds: PRE,
    });
    expect(plan.kind).toBe("proxy");
    expect(plan.offset).toBeCloseTo(0.951, 9);
  });

  it("secondary with no beep cannot be mapped: proxy at offset 0", () => {
    expect(
      planServedClip({
        index: 1,
        beepTime: null,
        processedTrim: false,
        primaryPeaksTrimmed: true,
        auditBeep: 5.0,
        preBufferSeconds: PRE,
      }),
    ).toEqual({ kind: "proxy", offset: 0 });
  });

  it("no audit beep (peaks not trimmed, no primary beep): offset 0", () => {
    expect(
      planServedClip({
        index: 1,
        beepTime: 5.951,
        processedTrim: true,
        primaryPeaksTrimmed: true,
        auditBeep: null,
        preBufferSeconds: PRE,
      }),
    ).toEqual({ kind: "trim", offset: 0 });
  });
});
