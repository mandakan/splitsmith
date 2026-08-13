/**
 * Decide whether a comment being composed at time `t` (seconds after the
 * beep) is about a specific shot or about a moment in time.
 *
 * The stored anchor always carries `anchor_t` regardless - the shot id
 * is a label, `t` is the truth. That is what makes a re-detect degrade a
 * shot-anchored comment to a time pin rather than re-attach it to a
 * different shot.
 */

import type { CoachShot } from "./api";

/**
 * Below the low end of the Production Optics split range the project
 * treats as typical (0.15-0.40 s), so a snap can never straddle two
 * adjacent shots in a fast string.
 */
export const SNAP_TOLERANCE_S = 0.12;

export type CommentAnchor = {
  anchor_kind: "time" | "shot";
  anchor_shot_id: string | null;
  shot_number: number | null;
};

const TIME_ANCHOR: CommentAnchor = {
  anchor_kind: "time",
  anchor_shot_id: null,
  shot_number: null,
};

export function snapToShot(
  tAfterBeep: number,
  shots: readonly CoachShot[],
  toleranceS: number = SNAP_TOLERANCE_S,
): CommentAnchor {
  let best: CoachShot | null = null;
  let bestDelta = Infinity;
  for (const shot of shots) {
    const delta = Math.abs(shot.time_from_beep - tAfterBeep);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = shot;
    }
  }
  // A shot with no stable id cannot be addressed, so it anchors by time.
  // Legacy audit docs that no save boundary has stamped hit this.
  if (best == null || bestDelta >= toleranceS || best.id == null) return TIME_ANCHOR;
  return { anchor_kind: "shot", anchor_shot_id: best.id, shot_number: best.shot_number };
}
