/**
 * "Play from beep": the one rule for auditioning a beep pick, shared by the
 * desktop beep step's band and the phone's beep card. Playback starts a
 * short lead before the line, so the ear hears silence and then the tone,
 * and stops after a short window, so a stage with the beep 25 s into the
 * source needs one press instead of a wait or a scrub.
 */

/** Seconds before the line that playback starts. */
export const AUDITION_LEAD_S = 0.4;
/** Seconds the audition plays before it pauses. */
export const AUDITION_WINDOW_S = 1.5;

/** Where an audition of a pick at ``beep`` (media seconds) starts, never
 *  before the media's own start. */
export function auditionStart(beep: number, lead: number = AUDITION_LEAD_S): number {
  return Math.max(0, beep - lead);
}

/** Whether a key press is the "Play from beep" shortcut: a plain B, no
 *  modifier (Cmd-B and friends stay the browser's). */
export function isAuditionKey(e: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey">): boolean {
  return (e.key === "b" || e.key === "B") && !e.metaKey && !e.ctrlKey && !e.altKey;
}
