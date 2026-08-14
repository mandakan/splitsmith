/**
 * The corpus list's filter predicate, shared between the /dev/corpus
 * table and the fixture detail page's prev/next navigation. One
 * implementation on purpose: the detail page re-derives the operator's
 * visible subset from the ``q`` / ``filter`` query params the list puts
 * on its row links, and walking a *differently*-filtered list than the
 * one on screen is exactly the bug (#898) this module exists to avoid.
 */
import type { LabFixtureRecord } from "@/lib/api";

export const FILTER_DEFS = [
  { key: "all", label: "all" },
  { key: "pending", label: "needs review" },
  { key: "promoted", label: "promoted" },
  { key: "audio-missing", label: "no audio" },
] as const;

export type FilterKey = (typeof FILTER_DEFS)[number]["key"];

export function isFilterKey(v: string | null): v is FilterKey {
  return FILTER_DEFS.some((f) => f.key === v);
}

export function filterFixtures(
  fixtures: LabFixtureRecord[],
  query: string,
  filter: FilterKey,
): LabFixtureRecord[] {
  const q = query.trim().toLowerCase();
  return fixtures.filter((fx) => {
    if (filter === "pending" && !fx.anchor_slug) return false;
    if (filter === "promoted" && !fx.anchor_slug) return false;
    if (filter === "audio-missing" && fx.has_audio) return false;
    if (!q) return true;
    return (
      fx.slug.toLowerCase().includes(q) ||
      (fx.source ?? "").toLowerCase().includes(q) ||
      (fx.event_id ?? "").toLowerCase().includes(q)
    );
  });
}
