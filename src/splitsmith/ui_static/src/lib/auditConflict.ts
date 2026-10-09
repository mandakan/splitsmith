import type { StageAudit } from "@/lib/api";

/**
 * What a rejected audit save (409 ``version_conflict``) means for the page.
 *
 * The server refuses a save whose ``_version`` no longer matches the stored
 * document. Most of the time the stored document differs from the copy the
 * page started from only in bookkeeping (the audit log, the revision): a
 * stale load landing late, a re-run that produced the same shots. Then the
 * save is replayed on the stored copy and nothing is asked. Only when the
 * shots, the beep or the candidates really moved does the operator choose.
 */

/** Fields that never decide whether two copies of a stage disagree. */
const BOOKKEEPING = new Set(["audit_events", "_version"]);

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      const v = (value as Record<string, unknown>)[key];
      if (v !== undefined) out[key] = canonical(v);
    }
    return out;
  }
  return value;
}

function content(doc: StageAudit | null): string {
  if (!doc) return "null";
  const kept = Object.fromEntries(Object.entries(doc).filter(([key]) => !BOOKKEEPING.has(key)));
  return JSON.stringify(canonical(kept));
}

/** True when ``stored`` differs from ``base`` in bookkeeping only, so a
 *  save built on ``base`` can be replayed on ``stored`` without losing
 *  anything either side holds. */
export function onlyBookkeepingMoved(base: StageAudit | null, stored: StageAudit | null): boolean {
  return content(base) === content(stored);
}
