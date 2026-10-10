/**
 * Coach and Breakdown's cameras for the PiP inset (#1409, epic #1405):
 * the coach payload's videos as ``PipCamera``s, primary first, each with
 * the inset stream ``insetStream`` picks after the kinds that already
 * failed for it on the page. Pure; ``StageVideo`` holds the state.
 */
import type { CoachVideoEntry } from "@/lib/api";
import { insetStream, type InsetStreamKind, type PipCamera } from "@/lib/pip";

export interface StageCamera extends PipCamera {
  video: CoachVideoEntry;
  /** The stream kind ``src`` was built from (echoed by ``onInsetError``). */
  insetKind: InsetStreamKind | null;
}

/** The payload's videos, primary first and otherwise in payload order,
 *  labelled "Cam 1", "Cam 2", ... in that order. The id is the path. */
export function stageCameras(
  videos: readonly CoachVideoEntry[],
  streamUrl: (video: CoachVideoEntry, kind: InsetStreamKind, version: string | null) => string,
  failed: Readonly<Record<string, ReadonlySet<InsetStreamKind>>> = {},
): StageCamera[] {
  const ordered = [...videos.filter((v) => v.role === "primary"), ...videos.filter((v) => v.role !== "primary")];
  return ordered.map((video, i) => {
    const s = insetStream(video, failed[video.path]);
    return {
      id: video.path,
      label: `Cam ${i + 1}`,
      primary: video.role === "primary",
      beepInClip: video.beep_in_clip,
      src: s ? streamUrl(video, s.kind, s.version) : null,
      unavailable: s === null,
      video,
      insetKind: s?.kind ?? null,
    };
  });
}

/** A primary-clip time in the big camera's clip: both clips line up on
 *  their own beep. ``bigBeep`` null is the primary itself (or no payload
 *  yet): unchanged. A moment before the big clip's start lands on its
 *  first frame, 0. */
export function primaryToBig(clip: number, primaryBeep: number | null, bigBeep: number | null): number {
  if (primaryBeep == null || bigBeep == null) return clip;
  return Math.max(0, clip - primaryBeep + bigBeep);
}

/** The big camera's clock in primary-clip seconds (``primaryToBig``'s
 *  inverse, without the clamp). */
export function bigToPrimary(videoTime: number, primaryBeep: number | null, bigBeep: number | null): number {
  if (primaryBeep == null || bigBeep == null) return videoTime;
  return videoTime - bigBeep + primaryBeep;
}
