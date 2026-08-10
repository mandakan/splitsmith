/**
 * Which file the audit page should stream for a camera, and how to map
 * audit-timeline time onto that file's clock.
 *
 * The audit timeline is the primary's served clip (trimmed when a trim
 * exists). Every other angle is served either as its own per-role trim
 * (``trimmed/stage{N}_cam_{video_id}_trimmed.mp4``) or, before a trim is
 * built, as the untrimmed source via the proxy kind (local mode falls back
 * to the source file on a proxy miss).
 *
 * The one non-obvious rule: per-role trims are each cut around their OWN
 * beep, so in trimmed space every angle's beep sits at
 * ``min(source_beep, pre_buffer)`` (see ui/audio.py). Mapping between two
 * trimmed clips therefore uses clip-local beeps - using source-space beep
 * deltas against a trimmed file seeks far past its end (black frame).
 */

export interface ServedClipPlan {
  /** Value for the stream endpoint's ``kind`` query. */
  kind: "trim" | "proxy";
  /** Seconds to ADD to an audit-timeline position to get the served
   *  clip's currentTime. Subtract to go the other way. */
  offset: number;
}

export function planServedClip(args: {
  /** 0 = the primary slot (owns the audit timeline). */
  index: number;
  /** This video's beep in source seconds, if any. */
  beepTime: number | null;
  /** True once this video's per-role trim has been built. */
  processedTrim: boolean;
  /** True when the audit timeline (peaks) is the trimmed primary. */
  primaryPeaksTrimmed: boolean;
  /** Beep position on the audit timeline (peaks.beep_time). */
  auditBeep: number | null;
  /** Project trim pre-buffer (seconds before the beep a trim keeps). */
  preBufferSeconds: number;
}): ServedClipPlan {
  const { index, beepTime, processedTrim, primaryPeaksTrimmed, auditBeep, preBufferSeconds } = args;

  if (index === 0) {
    // The primary's served clip IS the audit timeline; peaks already
    // reflect whichever file the server picked.
    return { kind: primaryPeaksTrimmed ? "trim" : "proxy", offset: 0 };
  }

  if (processedTrim && beepTime != null) {
    // Trimmed secondary: both clips are beep-anchored, so map through
    // clip-local beep positions.
    const clipBeep = Math.min(beepTime, preBufferSeconds);
    return { kind: "trim", offset: auditBeep != null ? clipBeep - auditBeep : 0 };
  }

  // Untrimmed secondary: the served file is the full source, so the map
  // is source-beep minus audit-beep. Without both beeps there is no
  // defensible mapping; park at offset 0 rather than guessing.
  const offset = beepTime != null && auditBeep != null ? beepTime - auditBeep : 0;
  return { kind: "proxy", offset };
}
