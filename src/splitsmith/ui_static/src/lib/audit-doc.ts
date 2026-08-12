import type { AuditMarker } from "@/components/MarkerLayer";
import type { AuditEvent, AuditShot, StageAudit } from "@/lib/api";

// Stale-base LWW race (#823): `base` is captured once on page-open (or
// stage change) and held for the life of the edit session - it is not
// refreshed by a mid-edit sync pull. If a pull lands while the operator
// is still editing, that pulled state is discarded: the next save here
// rebuilds from the held `base` and its local changes win the merge,
// silently reverting the pulled update. Accepted for the single-operator
// model - only one person edits a stage's audit at a time, so there is
// no concurrent writer to lose work to.
export interface BuildAuditJsonOptions {
  base: StageAudit | null;
  stage: { stage_number: number; stage_name: string; time_seconds: number };
  primaryBeepInClip: number | null;
  markers: AuditMarker[];
  appendEvents: AuditEvent[];
}

export function buildAuditJson(opts: BuildAuditJsonOptions): StageAudit {
  const { base, stage, primaryBeepInClip, markers, appendEvents } = opts;

  // Kept = detected + manual, sorted by time. Each gets a sequential
  // shot_number; we preserve the candidate_number when the marker came
  // from a detected candidate so the SSI cross-reference stays intact.
  const kept = markers
    .filter((m) => m.kind === "detected" || m.kind === "manual")
    .slice()
    .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));

  const shots: AuditShot[] = kept.map((m, i) => {
    const ms_after_beep =
      primaryBeepInClip != null ? Math.round((m.time - primaryBeepInClip) * 1000) : 0;
    return {
      shot_number: i + 1,
      candidate_number: m.candidateNumber,
      time: round3(m.time),
      ms_after_beep,
      source: m.kind === "manual" ? "manual" : "detected",
      ...(m.note ? { note: m.note } : {}),
      ...(m.shotId ? { id: m.shotId } : {}),
    } as AuditShot & { note?: string };
  });

  const previousEvents = base?.audit_events ?? [];
  const audit_events = [...previousEvents, ...appendEvents];

  return {
    ...(base ?? {}),
    stage_number: stage.stage_number,
    stage_name: stage.stage_name,
    stage_time_seconds: stage.time_seconds,
    beep_time: primaryBeepInClip ?? base?.beep_time,
    shots,
    _candidates_pending_audit: base?._candidates_pending_audit,
    audit_events,
  };
}

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
}

export function deriveMarkers(audit: StageAudit | null): AuditMarker[] {
  if (!audit) return [];
  const candidates = audit._candidates_pending_audit?.candidates ?? [];
  const shotsByCandidateNumber = new Map<number, true>();
  for (const s of audit.shots ?? []) {
    if (s.candidate_number != null) shotsByCandidateNumber.set(s.candidate_number, true);
  }
  const markers: AuditMarker[] = candidates.map((c) => ({
    id: `cand-${c.candidate_number}`,
    kind: shotsByCandidateNumber.has(c.candidate_number) ? "detected" : "rejected",
    time: c.time,
    candidateNumber: c.candidate_number,
    confidence: c.confidence ?? null,
    peakAmplitude: c.peak_amplitude ?? null,
    note: "",
    shotId: null,
  }));
  // Manual shots: those without a matching candidate_number.
  // Derived (promoted) fixtures may include shots with ``time: null`` for
  // anchor shots that the secondary couldn't snap; skip those here so the
  // marker drawer doesn't crash on ``time.toFixed(...)``.
  for (const s of audit.shots ?? []) {
    if (s.time == null) continue;
    if (s.candidate_number == null || s.source === "manual") {
      markers.push({
        id: s.id ?? `manual-shot-${s.shot_number}`,
        shotId: s.id ?? null,
        kind: "manual",
        time: s.time,
        candidateNumber: s.candidate_number ?? null,
        confidence: null,
        peakAmplitude: null,
        note: "",
      });
    }
  }
  return markers;
}
