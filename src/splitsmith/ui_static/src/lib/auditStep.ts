/**
 * Audit page derivation (UX PR 5, spec 2026-09-13 s4.4).
 *
 * Pure functions the page maps to primitives: which videos still need
 * step 1 (the beep), the flags-first shot list, the next flagged shot
 * for the `F` key, and the header's state chips. The page derives none
 * of this on its own, so the header chip, the list group and the key
 * cannot disagree about which shots are flagged.
 */
import type { AuditMarker } from "@/components/MarkerLayer";
import type { Anomaly } from "@/lib/anomalies";
import type { StageEntry, StageVideo } from "@/lib/api";

/** Videos on the stage that still need the beep confirmed: the primary
 *  when it has no beep or is unreviewed, then each secondary whose beep
 *  is unreviewed (ignored angles never queue). Empty means step 2. */
export function beepStepVideos(stage: StageEntry): StageVideo[] {
  const out: StageVideo[] = [];
  const primary = stage.videos.find((v) => v.role === "primary") ?? null;
  if (primary && (primary.beep_time == null || !primary.beep_reviewed)) out.push(primary);
  for (const v of stage.videos) {
    if (v.role !== "secondary") continue;
    if (!v.beep_reviewed) out.push(v);
  }
  return out;
}

export interface ShotRow {
  marker: AuditMarker;
  /** 1-based index in the kept-shot sequence; null for rejected candidates. */
  index: number | null;
  flag: string | null;
  rejected: boolean;
}

const MATCH_TOLERANCE_S = 0.02;

/** Anomaly text for a marker, matched by time the way the waveform pins
 *  are (shot numbers renumber on every keep / reject; times do not). */
function flagFor(marker: AuditMarker, anomalies: Anomaly[]): string | null {
  const hit = anomalies.find((a) => a.time != null && Math.abs(a.time - marker.time) <= MATCH_TOLERANCE_S);
  return hit ? hit.message : null;
}

/** The right-column list: flagged kept shots first (by time), then every
 *  marker by time with rejected candidates kept in place. */
export function shotRows(markers: AuditMarker[], anomalies: Anomaly[]): { flagged: ShotRow[]; all: ShotRow[] } {
  const sorted = markers.slice().sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
  let kept = 0;
  const all: ShotRow[] = sorted.map((marker) => {
    const rejected = marker.kind === "rejected";
    if (!rejected) kept += 1;
    return {
      marker,
      index: rejected ? null : kept,
      flag: rejected ? null : flagFor(marker, anomalies),
      rejected,
    };
  });
  return { flagged: all.filter((r) => r.flag != null), all };
}

/** Kept-shot index (0-based, for the page's current-shot state) of the
 *  next flagged shot after `current`, wrapping; null when none is flagged. */
export function nextFlaggedIndex(rows: ShotRow[], current: number): number | null {
  const flagged = rows.filter((r) => r.flag != null && r.index != null).map((r) => (r.index as number) - 1);
  if (flagged.length === 0) return null;
  return flagged.find((i) => i > current) ?? flagged[0];
}

export interface HeaderState {
  camera: string;
  beep: { label: string; tone: "neutral" | "warn"; tick: "movement" | "muted" };
  shots: string;
  flags: string | null;
}

const MOUNT_LABEL: Record<string, string> = {
  head: "Head cam",
  chest: "Chest cam",
  belt: "Belt cam",
  helmet: "Helmet cam",
  hand: "Hand cam",
  tripod: "Tripod",
  monopod: "Monopod",
  gimbal: "Gimbal",
};

/** The header's state chips: camera, beep, shot count, flag count. */
export function headerState(args: { primary: StageVideo; keptCount: number; flagCount: number }): HeaderState {
  const { primary, keptCount, flagCount } = args;
  const camera = (primary.camera_mount && MOUNT_LABEL[primary.camera_mount]) || "Head cam";
  let beep: HeaderState["beep"];
  if (primary.beep_time == null) {
    beep = { label: "No beep", tone: "warn", tick: "muted" };
  } else if (primary.beep_reviewed) {
    beep = { label: `Beep ${primary.beep_time.toFixed(2)} · confirmed`, tone: "neutral", tick: "movement" };
  } else {
    const conf = primary.beep_confidence != null ? ` · ${primary.beep_confidence.toFixed(2)}` : "";
    beep = { label: `Beep ${primary.beep_time.toFixed(2)} · unconfirmed${conf}`, tone: "warn", tick: "muted" };
  }
  return {
    camera,
    beep,
    shots: keptCount === 0 ? "no shots yet" : `${keptCount} ${keptCount === 1 ? "shot" : "shots"}`,
    flags: flagCount > 0 ? `${flagCount} ${flagCount === 1 ? "flag" : "flags"}` : null,
  };
}
