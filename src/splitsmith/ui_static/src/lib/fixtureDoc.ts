/**
 * The lab Review page's save: a fixture JSON rebuilt from the page's markers.
 *
 * A fixture's shots carry more than the page edits (``subclass``, an id,
 * ``interval_class``, a snapped shot's ``snap_displacement_ms``, times to
 * four decimals, ...). A shot the person did not move is written back
 * exactly as it was loaded, renumbered only; a moved one keeps its other
 * fields and gets the page's time. ``tests`` hold this over the whole
 * corpus (fixtureDoc.corpus.test.ts). Loading is ``lib/audit-doc.
 * deriveMarkers``, the same rule the production Audit uses.
 */

import type { AuditMarker } from "@/components/MarkerLayer";
import type { AuditEvent, AuditShot, StageAudit } from "@/lib/api";

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
}

/** The shot a marker was loaded from: by its stored id, a candidate's first
 *  claimant, or the shot its positional id names. ``null`` for a marker
 *  added since. */
function originalShot(m: AuditMarker, shots: ReadonlyArray<AuditShot>): AuditShot | null {
  if (m.shotId) return shots.find((s) => s.id === m.shotId) ?? null;
  if (m.id.startsWith("cand-") && m.candidateNumber != null) {
    return shots.find((s) => s.candidate_number === m.candidateNumber && s.time != null) ?? null;
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
    if (was && was.time === m.time && (!m.note || m.note === (was as { note?: string }).note)) {
      return was.shot_number === i + 1 || !("shot_number" in was) ? was : { ...was, shot_number: i + 1 };
    }
    // A moved shot is the person's, not the snap's: its displacement and its
    // ``promoted`` source describe a placement that no longer stands.
    const carried: Record<string, unknown> = { ...(was ?? {}) };
    delete carried.snap_displacement_ms;
    return {
      ...carried,
      shot_number: i + 1,
      ...(was == null || "candidate_number" in was || m.candidateNumber != null
        ? { candidate_number: m.candidateNumber }
        : {}),
      time: round3(m.time),
      ms_after_beep: beep != null ? Math.round((m.time - beep) * 1000) : 0,
      source: m.kind === "manual" ? "manual" : "detected",
      ...(m.note ? { note: m.note } : {}),
    };
  }) as AuditShot[];

  return {
    ...base,
    shots,
    audit_events: [...(base.audit_events ?? []), ...appendEvents],
  };
}
