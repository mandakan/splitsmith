/**
 * The lab Review page's save: a fixture JSON rebuilt from the page's markers.
 *
 * A fixture's shots carry more than the page edits (``subclass``, a snapped
 * shot's ``snap_displacement_ms``, ``sanity_flag``, ...). Each kept marker
 * finds the shot it was loaded from and keeps those fields; only what the
 * page owns (number, candidate, time, source) is rewritten. Loading is
 * ``lib/audit-doc.deriveMarkers``, the same rule the production Audit uses.
 */

import type { AuditMarker } from "@/components/MarkerLayer";
import type { AuditEvent, AuditShot, StageAudit } from "@/lib/api";

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
}

/** The shot a marker was loaded from: a candidate's first claimant, or the
 *  manual shot its positional id names. ``null`` for a marker added since. */
function originalShot(m: AuditMarker, shots: ReadonlyArray<AuditShot>): AuditShot | null {
  if (m.id.startsWith("cand-") && m.candidateNumber != null) {
    return shots.find((s) => s.candidate_number === m.candidateNumber) ?? null;
  }
  const n = /^manual-shot-(\d+)$/.exec(m.id);
  if (n) return shots.find((s) => s.shot_number === Number(n[1])) ?? null;
  return null;
}

export function buildFixtureJson(opts: {
  base: StageAudit;
  markers: AuditMarker[];
  appendEvents: AuditEvent[];
}): StageAudit {
  const { base, markers, appendEvents } = opts;
  const beep = base.beep_time ?? null;
  const before = base.shots ?? [];
  const kept = markers
    .filter((m) => m.kind === "detected" || m.kind === "manual")
    .slice()
    .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));

  const shots = kept.map((m, i) => {
    const was = originalShot(m, before);
    const time = round3(m.time);
    const moved = was == null || was.time == null || round3(was.time) !== time;
    // A moved shot is the person's, not the snap's: its displacement and its
    // ``promoted`` source describe a placement that no longer stands.
    const carried: Record<string, unknown> = { ...(was ?? {}) };
    if (moved) delete carried.snap_displacement_ms;
    return {
      ...carried,
      shot_number: i + 1,
      candidate_number: m.candidateNumber,
      time,
      ms_after_beep: beep != null ? Math.round((m.time - beep) * 1000) : 0,
      source: !moved && was?.source ? was.source : m.kind === "manual" ? "manual" : "detected",
      ...(m.note ? { note: m.note } : {}),
    };
  }) as AuditShot[];

  return {
    ...base,
    shots,
    audit_events: [...(base.audit_events ?? []), ...appendEvents],
  };
}
