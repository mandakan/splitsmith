/**
 * Secondary-camera options of the single-shooter match export (#193;
 * #974 item A): whether the per-cam trims ride the export at all, and
 * how they sit on the frame. Pure: the state shape, its defaults, the
 * mapper to the request body, and the one derivation the panel keys
 * off -- how many synced secondaries the selected stages actually have.
 *
 * "Synced" is the export's own rule: a secondary contributes only with a
 * beep (``ui/exports.py`` filters the rest out), so a stage whose second
 * camera never had its beep confirmed offers nothing here.
 */

import type { MatchExportRequestPayload, StageEntry } from "./api";

export type PipLayout = NonNullable<MatchExportRequestPayload["pip_layout"]>;

export interface CamOptions {
  /** Ship the per-cam trims alongside the primary. */
  includeSecondaries: boolean;
  /** `stacked` covers the primary full-frame on V2/V3; `pip-corners`
   *  scales each cam into a rotating corner. */
  pipLayout: PipLayout;
}

/** The server's own defaults (`include_secondaries` true, `stacked`). */
export const DEFAULT_CAM_OPTIONS: CamOptions = {
  includeSecondaries: true,
  pipLayout: "stacked",
};

/** Secondaries with a confirmed beep across the stages an export will
 *  take -- the count the panel shows, and zero is what hides it. */
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

/** The request-body slice. `pip_layout` is only meaningful with the cams
 *  on, and the server ignores it otherwise, so it always travels as
 *  chosen -- a user toggling cams back on gets the layout they picked. */
export function camExportFields(
  options: CamOptions,
): Pick<MatchExportRequestPayload, "include_secondaries" | "pip_layout"> {
  return { include_secondaries: options.includeSecondaries, pip_layout: options.pipLayout };
}
