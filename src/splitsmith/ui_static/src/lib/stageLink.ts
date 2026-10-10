/**
 * Deep links between Coach and Breakdown (#1377): the jump keeps the time
 * and the selection. ``?t=`` is seconds from the beep, ``&shot=`` a shot
 * number, ``&region=`` a region id (Breakdown selects it). A parameter that
 * does not parse, or names a shot or region the stage no longer has, is
 * ignored, never an error. Pure, no React.
 */
import type { CoachShot, StageEvent } from "@/lib/api";
import { shotAtOrBefore } from "@/lib/coachReview";

export interface StageLink {
  /** Seconds from the beep. */
  t: number | null;
  shot: number | null;
  region: string | null;
}

const REGION_ID = /^[A-Za-z0-9._-]{1,64}$/;

/** The query string for a link ("" when it carries nothing). */
export function stageLinkSearch(link: Partial<StageLink>): string {
  const params = new URLSearchParams();
  if (link.t != null && Number.isFinite(link.t)) params.set("t", String(Math.round(link.t * 1000) / 1000));
  if (link.shot != null && Number.isInteger(link.shot) && link.shot > 0) params.set("shot", String(link.shot));
  if (link.region && REGION_ID.test(link.region)) params.set("region", link.region);
  const s = params.toString();
  return s ? `?${s}` : "";
}

/** The link's parameters as written; anything malformed is null. */
export function parseStageLink(search: string): StageLink {
  const params = new URLSearchParams(search);
  const rawT = params.get("t");
  const t = rawT != null && rawT.trim() !== "" ? Number(rawT) : NaN;
  const rawShot = params.get("shot");
  const shot = rawShot != null && /^\d+$/.test(rawShot) ? Number(rawShot) : NaN;
  const region = params.get("region");
  return {
    t: Number.isFinite(t) ? t : null,
    shot: Number.isInteger(shot) && shot > 0 ? shot : null,
    region: region && REGION_ID.test(region) ? region : null,
  };
}

/**
 * Where a page opens for a link, against the stage as loaded: the shot and
 * region only when the stage still has them, the time from ``t``, else the
 * shot's time, else the region's start; the current shot is the linked one,
 * else the one the time has passed. ``null`` time: nothing to seek.
 */
export function resolveStageLink(
  link: StageLink,
  shots: readonly Pick<CoachShot, "shot_number" | "time_from_beep">[],
  events: readonly Pick<StageEvent, "id" | "start">[],
): { t: number | null; shot: number | null; region: string | null } {
  const shot = link.shot != null ? (shots.find((s) => s.shot_number === link.shot) ?? null) : null;
  const region = link.region != null ? (events.find((e) => e.id === link.region) ?? null) : null;
  const t = link.t ?? shot?.time_from_beep ?? region?.start ?? null;
  const active = shot?.shot_number ?? (t != null ? shotAtOrBefore(shots, t) : null);
  return { t, shot: active, region: region?.id ?? null };
}
