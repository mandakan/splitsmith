/**
 * Single takes damaged by the old stage-1 bug (mirror of the server's
 * ``MatchProject.take_damage``). A take registers one file on several
 * stages; before the fix a role change, unassign or move on one stage's
 * clip acted on the first stage's registration, leaving a covered stage
 * that lists the file twice and another with none. A covered stage that
 * does not hold exactly one entry of the file is damaged. The Footage page
 * offers the repair (``api.repairTake``).
 */
import type { MatchProject } from "@/lib/api";

const pad2 = (n: number) => String(n).padStart(2, "0");

export interface TakeDamage {
  storagePath: string;
  filename: string;
  /** Covered stages that need repair, ascending. */
  stages: number[];
}

export function damagedTakes(project: MatchProject): TakeDamage[] {
  const out: TakeDamage[] = [];
  for (const rv of project.raw_videos ?? []) {
    const covers = new Set(rv.covers_stages ?? []);
    if (covers.size === 0) continue;
    const stages = project.stages
      .filter((s) => covers.has(s.stage_number))
      .filter((s) => (s.videos ?? []).filter((v) => v.path === rv.storage_path).length !== 1)
      .map((s) => s.stage_number)
      .sort((a, b) => a - b);
    if (stages.length > 0) out.push({ storagePath: rv.storage_path, filename: rv.original_filename, stages });
  }
  return out;
}

/** The name the repair route addresses: the stored file under ``raw/``,
 *  which can differ from the uploaded ``filename`` shown to the user. */
export function storedFilename(d: TakeDamage): string {
  return d.storagePath.split("/").pop() ?? d.storagePath;
}

export function takeDamageText(d: TakeDamage): string {
  const ordinals = d.stages.map(pad2);
  const list =
    ordinals.length === 1
      ? `stage ${ordinals[0]}`
      : `stages ${ordinals.slice(0, -1).join(", ")} and ${ordinals[ordinals.length - 1]}`;
  return `${d.filename} is registered wrongly on ${list}.`;
}
