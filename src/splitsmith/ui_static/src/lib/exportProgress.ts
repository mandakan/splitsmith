/**
 * The export progress panel's derivations (spec 2026-10-25): the time-left
 * line, a row's time, whether the account's export limit is reached, the
 * export a refused start names, and the jobs drawer's one row per export.
 * Pure; the panel (`components/export/ExportProgress`) and `useJobs` map
 * these onto primitives.
 */

import { ApiError, type ActiveExports, type ExportRow, type ExportView, type Job } from "@/lib/api";

/** The export a 409 ``export_limit`` names (``ui/export_slot.ExportSummary``). */
export interface RunningExport {
  export_id: string;
  kind: string;
  label: string;
  match_id: string | null;
}

/** The kinds whose job leads an export (``ui/jobs.EXPORT_LEAD_KINDS``). */
const LEAD_KINDS = new Set(["compare-grid", "match_export", "highlight_reel"]);

function active(job: Job): boolean {
  return job.status === "pending" || job.status === "running";
}

/** One row per export: an export's jobs fold into its lead's (else its
 *  first job's), running while any of them runs, with their mean progress.
 *  Jobs no export queued pass through. Order follows each export's first job. */
export function foldExports(jobs: Job[]): Job[] {
  const groups = new Map<string, Job[]>();
  for (const job of jobs) {
    if (job.export_id) groups.set(job.export_id, [...(groups.get(job.export_id) ?? []), job]);
  }
  const out: Job[] = [];
  const seen = new Set<string>();
  for (const job of jobs) {
    if (!job.export_id) {
      out.push(job);
      continue;
    }
    if (seen.has(job.export_id)) continue;
    seen.add(job.export_id);
    const members = groups.get(job.export_id) ?? [job];
    const lead = members.find((j) => LEAD_KINDS.has(j.kind)) ?? members[0];
    const status = members.some((j) => j.status === "running")
      ? "running"
      : members.some(active)
        ? "pending"
        : lead.status;
    const progress = members.reduce((sum, j) => sum + (j.progress ?? 0), 0) / members.length;
    out.push({ ...lead, status, progress });
  }
  return out;
}

function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function minutes(seconds: number): string {
  if (seconds < 60) return "Under a minute";
  return `${Math.round(seconds / 60)} min`;
}

function hhmm(iso: string): string {
  const at = new Date(iso);
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}

/** "About 9 min left, done by 14:32"; no "about" once the estimate comes
 *  from history; "Taking longer than usual" when every part with work left
 *  has run past its estimate. Empty once the export ended. */
export function timeLeftLine(view: ExportView, _now: Date = new Date()): string {
  if (view.status !== "running" || view.time_left_s == null) return "";
  const open = view.rows.filter((r) => r.status === "running" || r.status === "waiting");
  if (open.length > 0 && open.every((r) => r.overrun)) return "Taking longer than usual";
  const left = minutes(view.time_left_s);
  const lead = view.approximate && view.time_left_s >= 60 ? `About ${left}` : left;
  return view.finish_at ? `${lead} left, done by ${hhmm(view.finish_at)}` : `${lead} left`;
}

/** A row's own time: elapsed while it runs, what it took once done. */
export function rowTimeLine(row: Pick<ExportRow, "status" | "elapsed_s">): string {
  if (row.elapsed_s == null) return "";
  if (row.status === "running") return clock(row.elapsed_s);
  if (row.status === "done" || row.status === "skipped") return `took ${clock(row.elapsed_s)}`;
  return "";
}

/** Whether the account may not start another export now. Desktop has no limit. */
export function limitReached(active: ActiveExports | null): boolean {
  return active != null && active.limit != null && active.exports.length >= active.limit;
}

/** The export a refused start names, or null for any other error. */
export function exportLimitRunning(err: unknown): RunningExport | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { code?: string; running?: RunningExport } | null;
  return body && body.code === "export_limit" && body.running ? body.running : null;
}
