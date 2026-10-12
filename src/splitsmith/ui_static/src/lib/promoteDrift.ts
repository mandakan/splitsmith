/**
 * The Promote stages panel's "Audit vs fixture" column: how each existing
 * fixture compares with its stage's audit now (``POST /api/lab/promote-drift``,
 * ``lab.core.audit_drift``), in words, and which rows that leaves worth
 * promoting again.
 */

export interface AuditDrift {
  state: "same" | "count" | "times";
  fixture_shots: number;
  audit_shots: number;
  moved: number;
}

/** The column's text; ``null`` drift is an existing fixture the server could
 *  not compare (no audit), ``undefined`` a row still loading. */
export function driftLabel(exists: boolean, drift: AuditDrift | null | undefined): string {
  if (!exists) return "new";
  if (drift === undefined) return "...";
  if (drift === null) return "not compared";
  if (drift.state === "same") return "same";
  if (drift.state === "times") return `${drift.moved} of ${drift.fixture_shots} times moved`;
  const d = drift.audit_shots - drift.fixture_shots;
  return d > 0
    ? `audit has ${d} more shot${d === 1 ? "" : "s"}`
    : `audit has ${-d} fewer shot${d === -1 ? "" : "s"}`;
}

/** Whether a row is worth promoting: no fixture yet, or the audit moved on.
 *  A fixture that still matches its audit would come back the same. */
export function worthPromoting(exists: boolean, drift: AuditDrift | null | undefined): boolean {
  return !exists || (drift != null && drift.state !== "same");
}
