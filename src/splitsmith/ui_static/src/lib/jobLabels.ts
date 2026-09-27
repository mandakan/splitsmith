/**
 * Human labels for job kinds. Lives in lib/ so the ProgressStrip primitive
 * and the Jobs surface can both read it without importing each other.
 */
import type { Job } from "@/lib/api";

export const KIND_LABEL: Record<string, string> = {
  detect_beep: "Detect beep",
  trim: "Trim stage video",
  shot_detect: "Detect shots",
  export: "Export stage",
  match_export: "Match export",
  audio_extract: "Audio extract",
  model_download: "Download models",
  generate_proxy: "Generating preview",
  sync_match: "Sync to hosted",
  auto_sync: "Auto-sync",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

/** What a job is working on, for the line under its kind. A stage or
 *  camera job names those; a job with neither (a match export, a sync)
 *  names its match. The match name also leads a stage job's line when
 *  the job belongs to another match than the one on screen, since the
 *  drawer lists every match's jobs. Empty when there is nothing to say
 *  (a model download), and the row then shows no line at all. */
export function jobTarget(
  job: Pick<Job, "match_id" | "stage_number" | "video_id">,
  ctx: { matchNames?: ReadonlyMap<string, string>; currentMatchId?: string | null } = {},
): string {
  const own: string[] = [];
  if (job.stage_number != null) own.push(`stage ${String(job.stage_number).padStart(2, "0")}`);
  if (job.video_id) own.push(`cam ${job.video_id.slice(0, 6)}`);
  const name = job.match_id ? ctx.matchNames?.get(job.match_id) : undefined;
  const elsewhere = job.match_id != null && job.match_id !== (ctx.currentMatchId ?? null);
  if (own.length === 0) return name ?? (job.match_id ? "whole match" : "");
  return name && elsewhere ? [name, ...own].join(" · ") : own.join(" · ");
}
