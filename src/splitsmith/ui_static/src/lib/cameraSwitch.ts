/**
 * The camera switch on a Compare tile: which cameras a shooter has on a
 * stage and what each is called. Pure; ``pages/compare/CameraMenu``
 * renders it.
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
