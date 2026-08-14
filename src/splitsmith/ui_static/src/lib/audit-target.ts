/**
 * Target resolution for the mobile audit screen. There is no selection
 * state: whichever marker falls inside the +/- TARGET_BAND_S band around
 * the playhead is the target. The band is fixed in time, not pixels, so
 * zoom never changes which marker it selects. A held id (set while
 * nudging) overrides the band until the playhead next moves - the page
 * owns that lifecycle; this module only honours the override.
 */
import type { AuditMarker } from "@/components/MarkerLayer";

export const TARGET_BAND_S = 0.12;

export type AuditTarget =
  | { kind: "shot"; marker: AuditMarker }
  | { kind: "candidate"; marker: AuditMarker }
  | { kind: "none" };

const isKept = (m: AuditMarker) => m.kind === "detected" || m.kind === "manual";

export function resolveTarget(
  markers: AuditMarker[],
  playhead: number,
  heldId: string | null = null,
  bandS: number = TARGET_BAND_S,
): AuditTarget {
  if (heldId != null) {
    const held = markers.find((m) => m.id === heldId);
    if (held) return { kind: isKept(held) ? "shot" : "candidate", marker: held };
  }
  const inBand = markers.filter((m) => Math.abs(m.time - playhead) <= bandS + 1e-10);
  const nearest = (ms: AuditMarker[]) =>
    ms.reduce((a, b) => (Math.abs(b.time - playhead) < Math.abs(a.time - playhead) ? b : a));
  const kept = inBand.filter(isKept);
  if (kept.length > 0) return { kind: "shot", marker: nearest(kept) };
  const rejected = inBand.filter((m) => m.kind === "rejected");
  if (rejected.length > 0) return { kind: "candidate", marker: nearest(rejected) };
  return { kind: "none" };
}
