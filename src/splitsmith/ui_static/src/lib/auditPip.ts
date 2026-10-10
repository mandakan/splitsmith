/**
 * Audit's cameras as PiP cameras (issue #1407): which clip each camera
 * streams in the inset and where its beep sits in that clip. The mapping
 * is Audit's own (``camPlayback.planServedClip``, the clip the big player
 * would stream for the camera), so a camera reads the same instant in the
 * inset as it does big. Pure; the page adds each camera's sync pill as
 * the inset's note.
 */
import { api, type StageVideo } from "@/lib/api";
import { planServedClip } from "@/lib/camPlayback";
import { insetStream, servedClipBeep, type InsetStreamKind, type PipCamera } from "@/lib/pip";

export interface AuditPipCamera extends PipCamera {
  /** The stream kind ``src`` was built from (``null`` when unavailable),
   *  echoed back through PipView's ``onInsetError``. */
  kind: InsetStreamKind | null;
}

export type FailedKinds = Readonly<Record<string, ReadonlySet<InsetStreamKind>>>;

export function auditPipCameras(args: {
  slug: string;
  stageNumber: number | null;
  /** Primary first, as the page orders them. */
  videos: readonly StageVideo[];
  /** The audit timeline (``null`` before peaks load: nothing lines up). */
  peaks: { beep_time: number | null; trimmed: boolean } | null;
  preBufferSeconds: number;
  /** Stream kinds that failed in the inset, per ``video_id``. */
  failed?: FailedKinds;
}): AuditPipCamera[] {
  const { slug, stageNumber, videos, peaks, preBufferSeconds, failed = {} } = args;
  const auditBeep = peaks?.beep_time ?? null;
  return videos.map((v, i) => {
    const plan = planServedClip({
      index: i,
      beepTime: v.beep_time,
      processedTrim: v.processed?.trim ?? false,
      primaryPeaksTrimmed: peaks?.trimmed ?? false,
      auditBeep,
      preBufferSeconds,
    });
    const s = insetStream(
      { kind: plan.kind, trim_version: v.trim_version, scrub_version: v.scrub_version },
      failed[v.video_id],
    );
    return {
      id: v.video_id,
      label: `Cam ${i + 1}`,
      primary: i === 0,
      beepInClip: servedClipBeep({ index: i, offset: plan.offset, auditBeep, beepTime: v.beep_time }),
      src: s ? api.videoStreamUrl(slug, v.path, s.kind, s.version, stageNumber) : null,
      unavailable: s === null,
      kind: s?.kind ?? null,
    };
  });
}

/** A camera's sync pill offset: a secondary's beep against the primary's,
 *  in source seconds; ``null`` for the primary (its pill shows its own
 *  beep time) or a camera without a beep. */
export function camOffset(
  video: Pick<StageVideo, "beep_time">,
  index: number,
  primaryBeepTime: number | null,
): number | null {
  return index === 0 || video.beep_time == null || primaryBeepTime == null ? null : video.beep_time - primaryBeepTime;
}

/** One more failed kind for a camera, returning the same map when it is
 *  already there. */
export function addFailedKind(failed: FailedKinds, id: string, kind: InsetStreamKind | null): FailedKinds {
  if (kind == null || failed[id]?.has(kind)) return failed;
  return { ...failed, [id]: new Set([...(failed[id] ?? []), kind]) };
}
