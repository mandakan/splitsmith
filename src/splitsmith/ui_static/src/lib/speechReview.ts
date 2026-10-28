/**
 * The fixture page's speech review (lab, #1778): which regions to show,
 * whether a set of them may be saved, and the summary line.
 *
 * The server re-checks every rule here (``lab/speech_review.py``); this
 * module only keeps the page from offering a save it would refuse.
 */

export interface SpeechSpan {
  start: number;
  end: number;
}

export type SpeechDecision = "masked" | "no_speech" | "excluded";

export interface SpeechReviewState {
  slug: string;
  duration: number;
  proposal: {
    method: string;
    masker_version: number;
    regions: SpeechSpan[];
    protected_overlaps: SpeechSpan[];
  } | null;
  masked_regions: (SpeechSpan & { method: string; masker_version: number })[];
  review: { decision: SpeechDecision; reviewed_at: string; masker_version: number } | null;
  protected: SpeechSpan[];
  masker_version: number;
  method: string;
}

/** What the editor starts from: the stored masks once a person has
 *  reviewed, else the masker's proposal. */
export function initialRegions(state: SpeechReviewState): SpeechSpan[] {
  const from = state.review ? state.masked_regions : (state.proposal?.regions ?? []);
  return sortSpans(from.map(({ start, end }) => ({ start, end })));
}

export function sortSpans(spans: SpeechSpan[]): SpeechSpan[] {
  return [...spans].sort((a, b) => a.start - b.start || a.end - b.end);
}

function overlaps(a: SpeechSpan, b: SpeechSpan): boolean {
  return a.start < b.end && a.end > b.start;
}

/** Why a region cannot be saved, or null. Mirrors the server's order. */
export function regionProblem(
  region: SpeechSpan,
  protectedSpans: SpeechSpan[],
  duration: number,
): string | null {
  if (!(region.end > region.start)) return "no length";
  if (duration > 0 && region.end > duration + 1e-6) return "past the clip";
  if (protectedSpans.some((p) => overlaps(region, p))) return "covers a shot or the beep";
  return null;
}

/** Every problem in the set, keyed by index; overlapping neighbours too. */
export function problems(
  regions: SpeechSpan[],
  protectedSpans: SpeechSpan[],
  duration: number,
): Map<number, string> {
  const out = new Map<number, string>();
  regions.forEach((r, i) => {
    const p = regionProblem(r, protectedSpans, duration);
    if (p) out.set(i, p);
  });
  const order = regions.map((r, i) => ({ r, i })).sort((a, b) => a.r.start - b.r.start);
  for (let k = 1; k < order.length; k++) {
    if (order[k].r.start < order[k - 1].r.end && !out.has(order[k].i)) out.set(order[k].i, "overlaps another");
  }
  return out;
}

/** Whether a decision can be saved with these regions. */
export function canSave(
  decision: SpeechDecision,
  regions: SpeechSpan[],
  protectedSpans: SpeechSpan[],
  duration: number,
): boolean {
  if (decision === "no_speech") return true;
  if (decision === "masked" && regions.length === 0) return false;
  return problems(regions, protectedSpans, duration).size === 0;
}

export function maskedSeconds(regions: SpeechSpan[]): number {
  return regions.reduce((sum, r) => sum + Math.max(0, r.end - r.start), 0);
}

export const DECISION_LABEL: Record<SpeechDecision, string> = {
  masked: "Masked",
  no_speech: "No speech",
  excluded: "Excluded",
};

/** The editor's regions differ from what the fixture stores. */
export function isDirty(state: SpeechReviewState, regions: SpeechSpan[]): boolean {
  if (!state.review) return true;
  const stored = sortSpans(state.masked_regions);
  const now = sortSpans(regions);
  return (
    stored.length !== now.length ||
    stored.some((s, i) => Math.abs(s.start - now[i].start) > 5e-4 || Math.abs(s.end - now[i].end) > 5e-4)
  );
}
