/**
 * Matches page derivations (spec 2026-09-13 s4.1). Pure functions from the
 * recent-projects detail list: the status counts, the search + status
 * filter, the Continue pick and what it says, the progress dots and the
 * touched time. `pages/Pick.tsx` maps the results to primitives.
 */
import type { NextStep, RecentProjectDetail } from "@/lib/api";
import type { PipelineState } from "@/components/ui/PipelineDots";

export type StatusFilter = "all" | "awaiting_footage" | "in_progress" | "exported" | "archived";

export type MatchCounts = Record<StatusFilter, number>;

/** Per-status counts. A missing folder is not a match; it is excluded
 *  from every count including "all". */
export function matchCounts(recents: RecentProjectDetail[]): MatchCounts {
  const c: MatchCounts = { all: 0, awaiting_footage: 0, in_progress: 0, exported: 0, archived: 0 };
  for (const r of recents) {
    if (r.kind === "missing") continue;
    c.all += 1;
    if (r.status === "awaiting_footage") c.awaiting_footage += 1;
    else if (r.status === "in_progress") c.in_progress += 1;
    else if (r.status === "exported") c.exported += 1;
    else if (r.status === "archived") c.archived += 1;
  }
  return c;
}

/** Search matches name and club; the path only counts on an install that
 *  has one (paths are never shown hosted, so they must not match there
 *  either -- a hit the user cannot see is a ghost row). */
export function filterMatches(
  recents: RecentProjectDetail[],
  query: string,
  status: StatusFilter,
  opts: { localFs: boolean } = { localFs: false },
): RecentProjectDetail[] {
  const q = query.trim().toLowerCase();
  return recents.filter((r) => {
    if (r.kind === "missing" && status !== "all") return false;
    if (status !== "all" && r.status !== status) return false;
    if (!q) return true;
    return (
      r.name.toLowerCase().includes(q) ||
      (r.club ?? "").toLowerCase().includes(q) ||
      (opts.localFs && r.path.toLowerCase().includes(q))
    );
  });
}

export function touchedAt(m: RecentProjectDetail): Date {
  return new Date(m.last_modified_at ?? m.last_opened_at);
}

/** The match the Continue card names: the most recently touched real
 *  match that is not archived and has a next step to name. */
export function pickContinue(recents: RecentProjectDetail[]): RecentProjectDetail | null {
  let best: RecentProjectDetail | null = null;
  for (const r of recents) {
    if (r.kind !== "match" || r.status === "archived" || !r.next_step || !r.match_id) continue;
    if (best === null || touchedAt(r).getTime() > touchedAt(best).getTime()) best = r;
  }
  return best;
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

/** The card's "Next:" line, e.g. "Audit stage 05 B5 Rear". */
export function continueLabel(step: NextStep): string {
  if (step.kind === "footage") return "Add footage";
  if (step.kind === "export") return "Export";
  const ordinal = step.stage_number != null ? ` stage ${pad2(step.stage_number)}` : "";
  const name = step.stage_name ? ` ${step.stage_name}` : "";
  return `Audit${ordinal}${name}`;
}

/** The card's button verb, shorter than the line: "Audit stage 05". */
export function continueVerb(step: NextStep): string {
  if (step.kind === "footage") return "Add footage";
  if (step.kind === "export") return "Export";
  return step.stage_number != null ? `Audit stage ${pad2(step.stage_number)}` : "Audit";
}

/** Where Continue lands once the match is bound. */
export function continueHref(m: RecentProjectDetail): string {
  const base = `/match/${m.match_id}`;
  const step = m.next_step;
  if (!step) return `${base}/`;
  if (step.kind === "footage") return `${base}/ingest`;
  if (step.kind === "export") return step.shooter_slug ? `${base}/export/${step.shooter_slug}` : `${base}/export`;
  if (step.shooter_slug && step.stage_number != null) {
    return `${base}/audit/${step.shooter_slug}/${step.stage_number}`;
  }
  return `${base}/audit`;
}

/** One dot per stage: audited ones done, the next one in progress while
 *  the match is in progress, the rest hollow. */
export function progressStates(m: RecentProjectDetail): PipelineState[] {
  const total = Math.max(0, m.stage_count);
  const done = Math.min(total, Math.max(0, m.stages_audited));
  return Array.from({ length: total }, (_, i) => {
    if (i < done) return "done";
    if (i === done && m.status === "in_progress" && m.video_count > 0) return "progress";
    return "todo";
  });
}

/** "27 Jun 2026" from an ISO date; the input when it does not parse. */
export function formatMatchDate(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** "just now", "12 min ago", "3 h ago", "2 d ago", "1 mo ago". */
export function formatRelative(then: Date, now: number = Date.now()): string {
  if (Number.isNaN(then.getTime())) return "—";
  const sec = Math.round((now - then.getTime()) / 1000);
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 45) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} d ago`;
  const mo = Math.round(day / 30);
  return `${mo} mo ago`;
}
