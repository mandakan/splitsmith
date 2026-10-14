/**
 * The beep review queue's order and position (agreed 2026-10-02): every
 * unconfirmed beep in the match, one after the other. Primaries come first
 * across the whole match, since they unlock trims and shot detection;
 * secondaries follow, since they never hold a stage back. Within each
 * group, stage order, then shooter order. "Later" and "Confirm & next"
 * both move to the beep after the current one, wrapping round, so a beep
 * left for later comes back at the end.
 */
import type { BeepQueueItem } from "./api";

export interface QueueKey {
  slug: string;
  stageNumber: number;
  videoId: string;
}

function same(item: BeepQueueItem, key: QueueKey): boolean {
  return (
    item.slug === key.slug &&
    item.stage_number === key.stageNumber &&
    item.video_id === key.videoId
  );
}

/** The unconfirmed beeps in review order. Shooter order is the order the
 *  queue lists shooters in (the match's own order). */
export function queueOrder(items: BeepQueueItem[]): BeepQueueItem[] {
  const shooterRank = new Map<string, number>();
  for (const it of items)
    if (!shooterRank.has(it.slug)) shooterRank.set(it.slug, shooterRank.size);
  return items
    .filter((it) => it.status !== "confirmed")
    .slice()
    .sort(
      (a, b) =>
        (a.role === "primary" ? 0 : 1) - (b.role === "primary" ? 0 : 1) ||
        a.stage_number - b.stage_number ||
        (shooterRank.get(a.slug) ?? 0) - (shooterRank.get(b.slug) ?? 0),
    );
}

/** Where ``key`` sits in ``order``: 1-based position and the total, or
 *  null when it is not waiting (confirmed, or not in the queue). */
export function queuePlace(
  order: BeepQueueItem[],
  key: QueueKey,
): { position: number; total: number } | null {
  const i = order.findIndex((it) => same(it, key));
  return i < 0 ? null : { position: i + 1, total: order.length };
}

/** The beep after ``key`` in ``order``, wrapping round; null when nothing
 *  else is waiting. A key not in the order (just confirmed elsewhere)
 *  starts from the front. */
export function nextInQueue(
  order: BeepQueueItem[],
  key: QueueKey,
): BeepQueueItem | null {
  const others = order.filter((it) => !same(it, key));
  if (others.length === 0) return null;
  const i = order.findIndex((it) => same(it, key));
  if (i < 0) return others[0];
  const after = order.slice(i + 1).find((it) => !same(it, key));
  return after ?? others[0];
}

/** "Beep 3 of 14" plus who, where and which camera. */
export function queueLine(
  item: BeepQueueItem,
  place: { position: number; total: number },
): string {
  const file = item.video_path.split("/").pop() ?? item.video_path;
  const stage = `Stage ${String(item.stage_number).padStart(2, "0")}`;
  return `Beep ${place.position} of ${place.total} · ${item.shooter_name} · ${stage} · ${file} · ${item.role}`;
}

/** Where a queued beep opens: its stage in Audit, on its camera. ``href``
 *  is the page's match-href builder. */
export function queueItemHref(
  item: BeepQueueItem,
  href: (...segments: string[]) => string,
): string {
  return `${href("audit", item.slug, String(item.stage_number))}?cam=${encodeURIComponent(item.video_id)}`;
}

/** The ``recheck`` value that walks the secondary beeps (Audit's
 *  ``?recheck=secondaries``). */
export const RECHECK_SECONDARIES = "secondaries";

/** The recheck pass over secondary beeps (2026-10-14): every secondary
 *  camera in the match, confirmed or not, in stage order then shooter
 *  order. A secondary's beep aligns its clip on the primary's shots, so a
 *  beep confirmed by eye a few tens of ms off is worth a second look. */
export function recheckOrder(items: BeepQueueItem[]): BeepQueueItem[] {
  const shooterRank = new Map<string, number>();
  for (const it of items)
    if (!shooterRank.has(it.slug)) shooterRank.set(it.slug, shooterRank.size);
  return items
    .filter((it) => it.role === "secondary")
    .slice()
    .sort(
      (a, b) =>
        a.stage_number - b.stage_number ||
        (shooterRank.get(a.slug) ?? 0) - (shooterRank.get(b.slug) ?? 0) ||
        a.video_id.localeCompare(b.video_id),
    );
}

/** The secondary after ``key`` in the recheck order; null after the last
 *  (the pass ends, it does not wrap round). */
export function recheckNext(
  order: BeepQueueItem[],
  key: QueueKey,
): BeepQueueItem | null {
  const i = order.findIndex((it) => same(it, key));
  return i < 0 ? (order[0] ?? null) : (order[i + 1] ?? null);
}

/** "Secondary beep 3 of 31" plus who, where and which file. */
export function recheckLine(
  item: BeepQueueItem,
  place: { position: number; total: number },
): string {
  const file = item.video_path.split("/").pop() ?? item.video_path;
  const stage = `Stage ${String(item.stage_number).padStart(2, "0")}`;
  return `Secondary beep ${place.position} of ${place.total} · ${item.shooter_name} · ${stage} · ${file}`;
}

/** A camera opened in the recheck pass; ``from`` (``<slug>/<stage>``) is
 *  the stage the pass returns to when it ends. */
export function recheckHref(
  item: BeepQueueItem,
  href: (...segments: string[]) => string,
  from: string,
): string {
  return `${queueItemHref(item, href)}&recheck=${RECHECK_SECONDARIES}&from=${encodeURIComponent(from)}`;
}

/** The entry wording wherever beeps wait: "Review beeps · 14 to confirm". */
export function reviewEntryText(pending: number): string {
  return `Review beeps · ${pending} to confirm`;
}
