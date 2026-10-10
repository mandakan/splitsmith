/**
 * The phone Audit's video dialog with a stage's other cameras stacked
 * under the primary (issue #1410, epic #1405). Pure: which cameras stack,
 * which two are on screen, the "2 / 3" chooser and where the opening seek
 * lands in each camera's clip. ``components/audit/mobile/CameraStack``
 * renders it; ``lib/auditPip`` builds the cameras.
 *
 * Two slots at most, whatever the camera count: the primary on top and one
 * other camera under it, cycled with the chooser when three or more line
 * up. Two live decodes is the PiP rule everywhere (a phone decoding three
 * 720p streams at once is the #1192 stall again), and two 16:9 frames are
 * what a 390 px portrait screen holds with room for the close row.
 */
import type { PipCamera } from "@/lib/pip";

/** The cameras the stack shows, primary first, or ``null`` when the
 *  dialog should stay the one-camera player it was: fewer than two
 *  cameras that can be lined up (a beep in their clip and something left
 *  to stream), or the primary itself cannot be. */
export function stackCameras<C extends PipCamera>(cameras: readonly C[]): C[] | null {
  const ok = cameras.filter((c) => c.beepInClip != null && c.src != null && c.unavailable !== true);
  if (ok.length < 2 || !ok[0].primary) return null;
  return ok;
}

export interface StackSlots<C extends PipCamera> {
  top: C;
  bottom: C;
  /** The bottom camera's place among all stacked cameras ("2 / 3"),
   *  ``null`` with two cameras (nothing to choose). */
  counter: { n: number; total: number } | null;
}

/** The two cameras on screen: the primary on top, ``shownId`` under it
 *  (the first other camera when ``shownId`` is not one of them). */
export function stackSlots<C extends PipCamera>(cameras: readonly C[], shownId: string | null): StackSlots<C> {
  const [top, ...others] = cameras;
  const idx = Math.max(
    0,
    others.findIndex((c) => c.id === shownId),
  );
  const bottom = others[idx];
  return {
    top,
    bottom,
    counter: cameras.length > 2 ? { n: idx + 2, total: cameras.length } : null,
  };
}

/** The next other camera for the chooser (wraps past the last). */
export function nextShown<C extends PipCamera>(cameras: readonly C[], shownId: string | null): string {
  const others = cameras.slice(1);
  const idx = Math.max(
    0,
    others.findIndex((c) => c.id === shownId),
  );
  return others[(idx + 1) % others.length].id;
}

/** A camera's clip time for an instant given in seconds from the beep. */
export function clipTimeFromBeep(fromBeep: number, camera: Pick<PipCamera, "beepInClip">): number {
  return Math.max(0, fromBeep + (camera.beepInClip ?? 0));
}
