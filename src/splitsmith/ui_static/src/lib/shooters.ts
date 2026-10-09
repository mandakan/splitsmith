/**
 * The Shooters page (spec 2026-10-09): everyone you have filmed, one row
 * per SSI shooter id, the look the videos draw. Pure wording and shape;
 * the page maps it onto primitives.
 */
import type { ShooterRosterRow } from "@/lib/api";

/** Two letters for an avatar: first and last name, else the first two. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/** Whether the row's look can be edited: the shooter book is keyed by the
 *  SSI shooter id, so a shooter without one cannot be in it. */
export function canEdit(row: ShooterRosterRow): boolean {
  return row.shooter_id !== null;
}

/** The line under a shooter's name. */
export function rowNote(row: ShooterRosterRow): string {
  if (row.shooter_id === null) {
    const where = row.source === "none" ? `In ${row.last_match_name}` : `Set in ${row.last_match_name}`;
    return `${where}. Link them to the scoreboard to give them a look of their own.`;
  }
  const matches = `${row.match_count} ${row.match_count === 1 ? "match" : "matches"}`;
  if (row.source === "none") return `${matches} · no look set`;
  return matches;
}

/** A stable React key: the SSI id, or the match and slug of a row without one. */
export function rowKey(row: ShooterRosterRow): string {
  return row.shooter_id !== null ? `id-${row.shooter_id}` : `m-${row.match_id ?? ""}-${row.slug ?? ""}`;
}
