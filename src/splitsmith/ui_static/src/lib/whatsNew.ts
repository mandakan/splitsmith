/**
 * What's new (``GET /api/whats-new``): which entries a user has not seen,
 * the sheet's month groups, and whether a feature's "New" chip still shows.
 * Seen is a set of entry ids plus ``chip:<key>`` dismissals, kept on the
 * server per install (local) or per account (hosted). Pure; the hook and
 * the sheet map it to primitives.
 */
import type { WhatsNewEntry } from "@/lib/api";

/** A "New" chip shows for this many days after its entry's date. */
export const CHIP_DAYS = 60;
export const CHIP_PREFIX = "chip:";

export function unseen(entries: readonly WhatsNewEntry[], seen: readonly string[]): WhatsNewEntry[] {
  const known = new Set(seen);
  return entries.filter((e) => !known.has(e.id));
}

const MONTH = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });

/** Month groups in the file's order (newest first). */
export function byMonth(entries: readonly WhatsNewEntry[]): { month: string; entries: WhatsNewEntry[] }[] {
  const groups: { month: string; entries: WhatsNewEntry[] }[] = [];
  for (const e of entries) {
    const month = MONTH.format(new Date(`${e.date}T00:00:00Z`));
    const last = groups[groups.length - 1];
    if (last && last.month === month) last.entries.push(e);
    else groups.push({ month, entries: [e] });
  }
  return groups;
}

/** The "New" chip on ``key``'s feature: an entry names it, it has not been
 *  dismissed, and the entry is under ``CHIP_DAYS`` old. Off while unknown. */
export function chipActive(
  entries: readonly WhatsNewEntry[] | null,
  seen: readonly string[] | null,
  key: string,
  today: Date = new Date(),
): boolean {
  if (!entries || !seen) return false;
  const entry = entries.find((e) => e.chip === key);
  if (!entry || seen.includes(`${CHIP_PREFIX}${key}`)) return false;
  const ageDays = (today.getTime() - new Date(`${entry.date}T00:00:00Z`).getTime()) / 86_400_000;
  return ageDays <= CHIP_DAYS;
}
