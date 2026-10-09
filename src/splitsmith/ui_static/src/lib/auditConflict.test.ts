import { describe, expect, it } from "vitest";

import type { StageAudit } from "@/lib/api";

import { onlyBookkeepingMoved } from "./auditConflict";

function doc(extra: Partial<StageAudit> = {}): StageAudit {
  return {
    stage_number: 3,
    stage_name: "Stage",
    stage_time_seconds: 12.15,
    beep_time: 5.95,
    shots: [{ shot_number: 1, candidate_number: 4, time: 7.31, ms_after_beep: 1360, source: "detected", id: "cand-4" }],
    audit_events: [{ ts: "2026-10-09T05:21:07Z", kind: "shot_detect_run", payload: {} }],
    ...extra,
  } as StageAudit;
}

describe("onlyBookkeepingMoved", () => {
  it("is true when only the audit log and the revision differ", () => {
    const base = { ...doc(), _version: "a" } as StageAudit;
    const stored = {
      ...doc({ audit_events: [...doc().audit_events!, { ts: "2026-10-09T05:21:08Z", kind: "save", payload: {} }] }),
      _version: "b",
    } as StageAudit;
    expect(onlyBookkeepingMoved(base, stored)).toBe(true);
  });

  it("ignores key order", () => {
    const base = doc();
    const stored = Object.fromEntries(Object.entries(doc()).reverse()) as StageAudit;
    expect(onlyBookkeepingMoved(base, stored)).toBe(true);
  });

  it("is false when a shot moved", () => {
    const stored = doc({
      shots: [{ shot_number: 1, candidate_number: 4, time: 7.4, ms_after_beep: 1450, source: "detected", id: "cand-4" }],
    } as Partial<StageAudit>);
    expect(onlyBookkeepingMoved(doc(), stored)).toBe(false);
  });

  it("is false when the beep moved", () => {
    expect(onlyBookkeepingMoved(doc(), doc({ beep_time: 6.1 }))).toBe(false);
  });

  it("is false when the stage was deleted or created under the page", () => {
    expect(onlyBookkeepingMoved(doc(), null)).toBe(false);
    expect(onlyBookkeepingMoved(null, doc())).toBe(false);
  });
});
