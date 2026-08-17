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
  { key: "not-in-model", label: "not in model" },
  { key: "audio-missing", label: "no audio" },
] as const;

export type FilterKey = (typeof FILTER_DEFS)[number]["key"];

export function isFilterKey(v: string | null): v is FilterKey {
  return FILTER_DEFS.some((f) => f.key === v);
}

/** Either promotion path: anchor block or the batch promote-stages stamp. */
export function isPromoted(fx: LabFixtureRecord): boolean {
  return Boolean(fx.anchor_slug || fx.promoted_at);
}

export function hasHumanLabels(fx: LabFixtureRecord): boolean {
  return fx.n_labeled_shots > 0 || fx.n_labeled_rejects > 0;
}

/** Mirror of the backend's ``FixtureRecord.needs_review``: anchor
 *  fixtures always pend (their labels are copies, the diff-confirm
 *  screen is their review); batch-promoted fixtures pend until a human
 *  label pass lands. */
export function needsReview(fx: LabFixtureRecord): boolean {
  if (fx.anchor_slug) return true;
  return Boolean(fx.promoted_at) && !hasHumanLabels(fx);
}

export function filterFixtures(
  fixtures: LabFixtureRecord[],
  query: string,
  filter: FilterKey,
): LabFixtureRecord[] {
  const q = query.trim().toLowerCase();
  return fixtures.filter((fx) => {
    if (filter === "pending" && !needsReview(fx)) return false;
    if (filter === "promoted" && !isPromoted(fx)) return false;
    if (filter === "not-in-model" && fx.in_calibration) return false;
    if (filter === "audio-missing" && fx.has_audio) return false;
    if (!q) return true;
    return (
      fx.slug.toLowerCase().includes(q) ||
      (fx.source ?? "").toLowerCase().includes(q) ||
      (fx.event_id ?? "").toLowerCase().includes(q)
    );
  });
}
