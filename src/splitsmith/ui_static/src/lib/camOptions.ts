/**
 * The cameras of the single-shooter match export (#193; #974 item A;
 * 2026-10-02): which camera is the picture, a second one inset in a
 * corner, and, for an editing timeline, whether the other angles ride
 * along switched off. Pure: the state shape, its defaults, the mapper to
 * the request body, and what the panel derives from the project -- how
 * many synced secondaries the selected stages have, and which cameras
 * there are to choose.
 *
 * A camera is named the way the server resolves it per stage
 * (``camera_select``): a mount ("hand", "head") or a role, the same mount
 * first, else the primary. "default" is the shooter's saved camera
 * (``compare_camera``, set from the share dialog or Compare).
 *
 * "Synced" is the export's own rule: a secondary contributes only with a
 * beep (``ui/exports.py`` filters the rest out), so a stage whose second
 * camera never had its beep confirmed offers nothing here.
 */

import type {
  MatchExportRequestPayload,
  ShooterListEntry,
  StageEntry,
} from "./api";
import { mountLabel } from "./shareCameras";

export type PipLayout = NonNullable<MatchExportRequestPayload["pip_layout"]>;
export type InsetCorner = NonNullable<
  MatchExportRequestPayload["inset_corner"]
>;
export type InsetSize = NonNullable<MatchExportRequestPayload["inset_size"]>;

export interface CamOptions {
  /** Editing timelines (FCPXML, FCP 7): carry every other angle on its
   *  own track, switched off. An MP4 never carries them. */
  includeSecondaries: boolean;
  /** Superseded by the inset; kept so an old preset's ``pip-corners``
   *  can be read back as an inset (``fromPipLayout``). Always sent as
   *  ``stacked``. */
  pipLayout: PipLayout;
  /** "default" | "primary" | a mount. */
  mainCamera: string;
  /** null: no inset. */
  insetCamera: string | null;
  insetCorner: InsetCorner;
  insetSize: InsetSize;
}

/** The server's own defaults: the saved camera, no inset, the other
 *  angles carried for editing. */
export const DEFAULT_CAM_OPTIONS: CamOptions = {
  includeSecondaries: true,
  pipLayout: "stacked",
  mainCamera: "default",
  insetCamera: null,
  insetCorner: "bottom-right",
  insetSize: "medium",
};

/** An old preset's rotating corners read as the one inset it stood for:
 *  the first secondary, bottom-left (the server maps it the same way). */
export function fromPipLayout(
  options: CamOptions,
  layout: PipLayout | undefined,
): CamOptions {
  if (layout === "pip-corners" && options.insetCamera === null) {
    return { ...options, insetCamera: "secondary", insetCorner: "bottom-left" };
  }
  return options;
}

/** Secondaries with a confirmed beep across the stages an export will
 *  take -- zero hides the camera rows. */
export function syncedSecondaryCount(
  stages: ReadonlyArray<Pick<StageEntry, "stage_number" | "videos">>,
  stageNumbers?: ReadonlyArray<number>,
): number {
  const wanted = stageNumbers === undefined ? null : new Set(stageNumbers);
  let count = 0;
  for (const stage of stages) {
    if (wanted !== null && !wanted.has(stage.stage_number)) continue;
    for (const video of stage.videos) {
      if (video.role === "secondary" && video.beep_time !== null) count += 1;
    }
  }
  return count;
}

export interface CameraChoice {
  value: string;
  label: string;
}

/** The cameras the selected stages have, as choices: the primary, each
 *  mount a synced camera carries, and "Secondary" when a secondary has no
 *  mount (the role is the only way to name it). */
export function cameraChoices(
  stages: ReadonlyArray<Pick<StageEntry, "stage_number" | "videos">>,
  stageNumbers?: ReadonlyArray<number>,
): CameraChoice[] {
  const wanted = stageNumbers === undefined ? null : new Set(stageNumbers);
  const mounts = new Set<string>();
  let unmountedSecondary = false;
  for (const stage of stages) {
    if (wanted !== null && !wanted.has(stage.stage_number)) continue;
    for (const video of stage.videos) {
      if (video.role === "ignored" || video.beep_time === null) continue;
      const mount = video.camera_mount;
      if (mount) mounts.add(mount);
      else if (video.role === "secondary") unmountedSecondary = true;
    }
  }
  return [
    { value: "primary", label: "Primary" },
    ...[...mounts].sort().map((m) => ({ value: m, label: mountLabel(m) })),
    ...(unmountedSecondary ? [{ value: "secondary", label: "Secondary" }] : []),
  ];
}

/** The request-body slice. */
export function camExportFields(
  options: CamOptions,
): Pick<
  MatchExportRequestPayload,
  | "include_secondaries"
  | "pip_layout"
  | "main_camera"
  | "inset_camera"
  | "inset_corner"
  | "inset_size"
> {
  return {
    include_secondaries: options.includeSecondaries,
    pip_layout: "stacked",
    main_camera: options.mainCamera,
    inset_camera: options.insetCamera,
    inset_corner: options.insetCorner,
    inset_size: options.insetSize,
  };
}

/** The choice in words for the summary rail: "Handheld + Head cam inset". */
export function camsSummary(
  options: CamOptions,
  choices: CameraChoice[],
  savedLabel: string,
): string {
  const name = (value: string) =>
    choices.find((c) => c.value === value)?.label ?? value;
  const main =
    options.mainCamera === "default" ? savedLabel : name(options.mainCamera);
  return options.insetCamera === null
    ? main
    : `${main} + ${name(options.insetCamera)} inset`;
}

/** The grid's inset choices: the primary and every mount any shooter's
 *  cameras carry (one selector for the whole grid, resolved per shooter). */
export function gridCameraChoices(
  shooters: ReadonlyArray<Pick<ShooterListEntry, "cameras">>,
): CameraChoice[] {
  const mounts = new Set<string>();
  for (const s of shooters)
    for (const c of s.cameras ?? []) if (c.mount) mounts.add(c.mount);
  return [
    { value: "primary", label: "Primary" },
    ...[...mounts].sort().map((m) => ({ value: m, label: mountLabel(m) })),
  ];
}

/** Shooters with more than one camera: zero hides the grid's inset row. */
export function gridMultiCamShooters(
  shooters: ReadonlyArray<Pick<ShooterListEntry, "cameras">>,
): number {
  return shooters.filter((s) => (s.cameras ?? []).length > 1).length;
}
