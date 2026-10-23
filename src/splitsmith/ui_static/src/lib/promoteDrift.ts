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

/** What a re-promote carried from the fixture it overwrote
 *  (``lab.core.LabelCarry``). */
export interface LabelCarry {
  reasons_carried: number;
  subclasses_carried: number;
  walk_decisions_carried: number;
  reasons_dropped: DroppedLabel[];
  subclasses_dropped: DroppedLabel[];
}

export interface DroppedLabel {
  /** On the old fixture's clip, in seconds. */
  time: number;
  label: string;
  why: string;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** A promoted row's report: what the lab labels did, every dropped one by
 *  time so nothing disappears silently. ``null`` for a first promote. */
export function labelCarryLabel(carry: LabelCarry | null | undefined): string | null {
  if (!carry) return null;
  const kept = `kept ${plural(carry.reasons_carried, "reason", "reasons")}, ${plural(carry.subclasses_carried, "subclass", "subclasses")}`;
  const dropped = [...carry.reasons_dropped, ...carry.subclasses_dropped]
    .sort((a, b) => a.time - b.time)
    .map((d) => `${d.time.toFixed(3)} s ${d.label} (${d.why})`);
  return dropped.length === 0 ? kept : `${kept}; dropped ${dropped.join(", ")}`;
}
