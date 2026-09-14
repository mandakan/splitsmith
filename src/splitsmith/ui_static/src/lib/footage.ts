/**
 * Footage derivation (UX PR 6, spec 2026-09-13 s4.3).
 *
 * Pure functions from the per-shooter project payloads (plus the shooter
 * list and the shell's jobs) to the coverage matrix: one row per stage,
 * one cell per shooter with its files and the primary's beep state; the
 * unassigned tray; the header counts. The page renders these and owns
 * only the fetches and the writes.
 */
import type { Job, MatchProject, ShooterListEntry, StageVideo } from "@/lib/api";
import { isJobActive } from "@/lib/jobs";

export interface FootageCell {
  slug: string;
  shooterName: string;
  /** Every video on the stage, ignored ones included (the chip shows the role). */
  videos: StageVideo[];
  primary: StageVideo | null;
  beep: { time: number | null; reviewed: boolean; detecting: boolean };
}

export interface FootageRow {
  stageNumber: number;
  stageName: string;
  /** One per shooter with a loaded project, in shooter-list order. */
  cells: FootageCell[];
  /** Every shooter with a project has a primary on this stage. */
  covered: boolean;
}

export interface UnassignedItem {
  slug: string;
  shooterName: string;
  video: StageVideo;
  recordedAt: string | null;
}

function detecting(jobs: Job[], slug: string, stageNumber: number): boolean {
  return jobs.some(
    (j) => isJobActive(j) && j.kind === "detect_beep" && j.shooter_slug === slug && j.stage_number === stageNumber,
  );
}

export function buildFootageRows(args: {
  projects: Record<string, MatchProject | null>;
  shooters: ShooterListEntry[];
  jobs: Job[];
}): FootageRow[] {
  const { projects, shooters, jobs } = args;
  const withProject = shooters.filter((s) => projects[s.slug]);
  const stages = new Map<number, string>();
  for (const s of withProject) {
    for (const st of projects[s.slug]!.stages) {
      if (st.placeholder) continue;
      if (!stages.has(st.stage_number)) stages.set(st.stage_number, st.stage_name);
    }
  }
  return [...stages.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([stageNumber, stageName]) => {
      const cells: FootageCell[] = withProject.map((s) => {
        const entry = projects[s.slug]!.stages.find((st) => st.stage_number === stageNumber);
        const videos = entry?.videos ?? [];
        const primary = videos.find((v) => v.role === "primary") ?? null;
        return {
          slug: s.slug,
          shooterName: s.name,
          videos,
          primary,
          beep: {
            time: primary?.beep_time ?? null,
            reviewed: primary?.beep_reviewed ?? false,
            detecting: detecting(jobs, s.slug, stageNumber),
          },
        };
      });
      return { stageNumber, stageName, cells, covered: cells.length > 0 && cells.every((c) => c.primary != null) };
    });
}

/** Every project's unassigned tray, capture order within a shooter
 *  (timestamp-less files last), shooters in list order. */
export function unassignedVideos(args: {
  projects: Record<string, MatchProject | null>;
  shooters: ShooterListEntry[];
}): UnassignedItem[] {
  const out: UnassignedItem[] = [];
  for (const s of args.shooters) {
    const p = args.projects[s.slug];
    if (!p) continue;
    const items = (p.unassigned_videos ?? []).map((video) => ({
      slug: s.slug,
      shooterName: s.name,
      video,
      recordedAt: video.match_timestamp ?? null,
    }));
    items.sort((a, b) => {
      if (a.recordedAt && b.recordedAt) return a.recordedAt.localeCompare(b.recordedAt);
      if (a.recordedAt) return -1;
      if (b.recordedAt) return 1;
      return 0;
    });
    out.push(...items);
  }
  return out;
}

export interface FootageStats {
  shooters: number;
  videos: number;
  covered: number;
  total: number;
  unassigned: number;
}

/** Header counts: shooters, every video (assigned or not), covered stages
 *  over all stages, unassigned files. */
export function footageStats(rows: FootageRow[], unassigned: UnassignedItem[], shooterCount: number): FootageStats {
  const assigned = rows.reduce((n, r) => n + r.cells.reduce((m, c) => m + c.videos.length, 0), 0);
  return {
    shooters: shooterCount,
    videos: assigned + unassigned.length,
    covered: rows.filter((r) => r.covered).length,
    total: rows.length,
    unassigned: unassigned.length,
  };
}

export type BeepTone = "ok" | "warn" | "none";

/** The beep column: the confirmed time, an amber label while detecting
 *  or unconfirmed, a dash without a primary or a beep. */
export function beepState(cell: FootageCell): { label: string; tone: BeepTone; confirmable: boolean } {
  if (!cell.primary) return { label: "—", tone: "none", confirmable: false };
  if (cell.beep.detecting) return { label: "detecting…", tone: "warn", confirmable: false };
  if (cell.beep.time == null) return { label: "no beep", tone: "warn", confirmable: false };
  if (!cell.beep.reviewed) return { label: `${cell.beep.time.toFixed(2)} · unconfirmed`, tone: "warn", confirmable: true };
  return { label: cell.beep.time.toFixed(2), tone: "ok", confirmable: false };
}

/** `VID_20260627_1403.MP4` -> `VID_…1403.MP4`: the first four and the
 *  last four characters of the stem when it runs past 14. */
export function shortName(path: string): string {
  const base = path.split("/").pop() ?? path;
  const dot = base.lastIndexOf(".");
  const stem = dot > 0 ? base.slice(0, dot) : base;
  const ext = dot > 0 ? base.slice(dot) : "";
  if (stem.length <= 14) return base;
  return `${stem.slice(0, 4)}…${stem.slice(-4)}${ext}`;
}
