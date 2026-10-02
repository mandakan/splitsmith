/**
 * The camera switch on a Compare tile: which cameras a shooter has on a
 * stage, what each is called, and the line that says there is more than
 * one. Pure; ``pages/compare/CameraSwitch`` renders it.
 */
import type { CoachVideoEntry } from "./api";

export interface CameraOption {
  index: number;
  label: string;
  /** A camera without a beep cannot be lined up with the others. */
  disabled: boolean;
}

/** One option per camera, primary first, as the coach payload orders them.
 *  An older server sends no label: the primary is "Primary", the rest are
 *  numbered. */
export function cameraOptions(cams: CoachVideoEntry[]): CameraOption[] {
  return cams.map((c, i) => ({
    index: i,
    label: c.label || (i === 0 ? "Primary" : `Camera ${i + 1}`),
    disabled: c.beep_in_clip == null,
  }));
}

/** "3 angles" when there is anything to switch to, else null. */
export function angleCountText(cams: CoachVideoEntry[] | null): string | null {
  return cams && cams.length > 1 ? `${cams.length} angles` : null;
}
