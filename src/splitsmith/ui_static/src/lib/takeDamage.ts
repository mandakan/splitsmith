/**
 * Single takes damaged by the old stage-1 bug (mirror of the server's
 * ``MatchProject.take_damage``). Before the fix, a role change or move on
 * one stage's clip of a take appended the first stage's registration to
 * the clicked stage, which then lists the file twice. That is the only
 * unambiguous trace: an empty covered stage looks exactly like a
 * legitimate remove, so it is never damage, only a hint after a repair
 * ("may need its clip re-assigned"). The Footage page offers the repair
 * (``api.repairTake``), which keeps each stage's first entry.
 */
import type { MatchProject } from "@/lib/api";

const pad2 = (n: number) => String(n).padStart(2, "0");

export interface TakeDamage {
  storagePath: string;
  /** The name to show: the uploaded name when the take has a record. */
  filename: string;
  /** Stages listing the file more than once, ascending. */
  stages: number[];
  /** Covered stages with no entry for the file (a hint, not damage). */
  unplaced: number[];
}

export function damagedTakes(project: MatchProject): TakeDamage[] {
  const dupStages = new Map<string, number[]>();
  for (const stage of project.stages) {
    const counts = new Map<string, number>();
    for (const v of stage.videos ?? []) counts.set(v.path, (counts.get(v.path) ?? 0) + 1);
    for (const [path, n] of counts) {
      if (n > 1) dupStages.set(path, [...(dupStages.get(path) ?? []), stage.stage_number]);
    }
  }
  return [...dupStages.entries()].map(([storagePath, stages]) => {
    const rv = (project.raw_videos ?? []).find((r) => r.storage_path === storagePath);
    const unplaced = project.stages
      .filter((s) => (rv?.covers_stages ?? []).includes(s.stage_number))
      .filter((s) => !(s.videos ?? []).some((v) => v.path === storagePath))
      .map((s) => s.stage_number)
      .sort((a, b) => a - b);
    return {
      storagePath,
      filename: rv?.original_filename ?? storedName(storagePath),
      stages: [...stages].sort((a, b) => a - b),
      unplaced,
    };
  });
}

function storedName(storagePath: string): string {
  return storagePath.split("/").pop() ?? storagePath;
}

/** The name the repair route addresses: the stored file under ``raw/``,
 *  which can differ from the uploaded ``filename`` shown to the user. */
export function storedFilename(d: TakeDamage): string {
  return storedName(d.storagePath);
}

function stageList(stages: number[]): string {
  const o = stages.map(pad2);
  return o.length === 1 ? `stage ${o[0]}` : `stages ${o.slice(0, -1).join(", ")} and ${o[o.length - 1]}`;
}

export function takeDamageText(d: TakeDamage): string {
  const head = `${d.filename} is listed twice on ${stageList(d.stages)}.`;
  if (d.unplaced.length === 0) return head;
  const extras = d.stages.length === 1 ? "the extra entry" : "the extra entries";
  const their = d.unplaced.length === 1 ? "its" : "their";
  return `${head} Repair removes ${extras}; ${stageList(d.unplaced)} may need ${their} clip re-assigned.`;
}
