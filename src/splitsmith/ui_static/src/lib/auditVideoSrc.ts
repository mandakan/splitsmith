/**
 * The URL the Audit page's player streams for the active camera.
 *
 * The served file is pinned to the plan's kind for the life of the
 * <video> element (see lib/camPlayback.ts): a trim job finishing mid-play
 * would otherwise flip the server's ``auto`` pick and the next Range
 * request would fall past the shorter trim's EOF. So nothing streams
 * until peaks say which kind the timeline is in; when peaks failed (no
 * beep yet) ``auto`` is right because no trim can exist without a beep.
 * A pinned trim goes through the scrub source (lib/scrubSource.ts), which
 * may serve its 720p rendition and carries the version that remounts the
 * player after a re-cut. Every URL names the stage: a multi-stage single
 * take registers one source on several stages, each with its own trim.
 */
import { api, type StageVideo } from "@/lib/api";
import type { ServedClipPlan } from "@/lib/camPlayback";
import type { ScrubChoice } from "@/lib/scrubSource";

type AuditVideo = Pick<StageVideo, "path" | "trim_version" | "scrub_version">;

export function auditVideoSrc<V extends AuditVideo>(args: {
  slug: string;
  video: V | null | undefined;
  plan: ServedClipPlan | null;
  peaksLoaded: boolean;
  peaksFailed: boolean;
  stageNumber: number | null;
  choose: (video: V) => ScrubChoice;
}): string {
  const { slug, video, plan, peaksLoaded, peaksFailed, stageNumber, choose } = args;
  if (!video || !plan) return "";
  if (!peaksLoaded) return peaksFailed ? api.videoStreamUrl(slug, video.path, "auto", null, stageNumber) : "";
  if (plan.kind === "trim") {
    const choice = choose(video);
    return api.videoStreamUrl(slug, video.path, choice.kind, choice.version, stageNumber);
  }
  return api.videoStreamUrl(slug, video.path, plan.kind, null, stageNumber);
}
