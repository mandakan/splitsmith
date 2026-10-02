/**
 * The share dialog's camera defaults (2026-10-02): which angle each
 * shooter starts on for every viewer of the match's share links, the
 * Compare grid and the export grid. The value is the shooter's
 * ``compare_camera``, a mount ("hand") or nothing for the primary,
 * resolved per stage like ``camera_select``; viewers can still switch
 * while watching. Pure; ``components/results/ShareCameras`` renders it.
 */
import type { ShooterCameraInfo } from "./api";

/** The Segmented value for "start on the primary" (stored as null). */
export const PRIMARY = "primary";

const MOUNT_LABEL: Record<string, string> = {
  head: "Head cam",
  hand: "Handheld",
  chest: "Chest cam",
  helmet: "Helmet cam",
  belt: "Belt cam",
  gimbal: "Gimbal",
  tripod: "Tripod",
  monopod: "Monopod",
};

export function mountLabel(selector: string): string {
  if (selector === PRIMARY) return "Primary";
  return MOUNT_LABEL[selector] ?? `${selector.charAt(0).toUpperCase()}${selector.slice(1)}`;
}

/** What a shooter can start on: one entry per camera mount, the primary
 *  camera's first. A camera without a mount is reachable only as "the
 *  primary", so that entry is added when one exists. A shooter with a
 *  single camera has nothing to choose (one entry). */
export function cameraChoices(cameras: ShooterCameraInfo[]): string[] {
  const primaryFirst = [...cameras].sort((a, b) => (a.role === "primary" ? 0 : 1) - (b.role === "primary" ? 0 : 1));
  const mounts = [...new Set(primaryFirst.map((c) => c.mount).filter((m): m is string => Boolean(m)))];
  const unmounted = cameras.some((c) => !c.mount);
  return unmounted ? [PRIMARY, ...mounts] : mounts;
}

/** The saved default as a choice. Nothing saved means the primary: shown
 *  as the primary camera's mount when it has one. A value no camera
 *  carries any more resolves to the primary too. */
export function currentChoice(
  saved: string | null | undefined,
  choices: string[],
  cameras: ShooterCameraInfo[] = [],
): string {
  if (saved && choices.includes(saved)) return saved;
  const primaryMount = cameras.find((c) => c.role === "primary")?.mount;
  if (primaryMount && choices.includes(primaryMount)) return primaryMount;
  return choices.includes(PRIMARY) ? PRIMARY : (choices[0] ?? PRIMARY);
}

export interface ShooterCameras {
  slug: string;
  name: string;
  choices: string[];
}

/** "Everyone on X": the shooters that have X change, the rest keep what
 *  they have and are named, so the dialog can say so. */
export function applyToAll(
  shooters: ShooterCameras[],
  selector: string,
): { changes: { slug: string; selector: string }[]; without: string[] } {
  const changes: { slug: string; selector: string }[] = [];
  const without: string[] = [];
  for (const s of shooters) {
    if (s.choices.includes(selector)) changes.push({ slug: s.slug, selector });
    else without.push(s.name);
  }
  return { changes, without };
}

/** The options for the "Everyone" row: the primary, then every mount
 *  any shooter has. "Primary" here means "back to each shooter's primary". */
export function allChoices(shooters: ShooterCameras[]): string[] {
  const mounts = [...new Set(shooters.flatMap((s) => s.choices.filter((c) => c !== PRIMARY)))].sort();
  return [PRIMARY, ...mounts];
}

/** "Everyone on the primary" clears every saved default. */
export function everyoneToPrimary(shooters: ShooterCameras[]): { slug: string; selector: string }[] {
  return shooters.map((s) => ({ slug: s.slug, selector: PRIMARY }));
}
