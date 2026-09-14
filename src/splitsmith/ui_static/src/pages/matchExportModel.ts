/**
 * Plain logic behind the match-scoped compare-grid export page
 * (``MatchExport.tsx``, phase 0). Split out from the component so both
 * it and its test can build the request payload / summarise the job
 * result without going through React.
 */

import type { CompareGridRequestPayload, CompareGridResult } from "@/lib/api";
import { anyRenderOptionOn, gridExportFields, type RenderOptions } from "@/lib/renderOptions";

export interface CanvasChoice {
  id: "uhd" | "hd";
  label: string;
  width: number;
  height: number;
}

/** Canvas size options for the render. ``[0]`` (4K UHD) is the page's
 *  default; 1080p is offered as the faster alternative. Order matters --
 *  callers index ``CANVAS_CHOICES[0]`` for the default selection. */
export const CANVAS_CHOICES: readonly CanvasChoice[] = [
  { id: "uhd", label: "4K UHD (3840x2160)", width: 3840, height: 2160 },
  { id: "hd", label: "1080p (1920x1080) -- faster", width: 1920, height: 1080 },
] as const;

/** Build the POST /api/match/compare-export body from the page's form
 *  state. Stage numbers are sorted ascending -- the chip selector's
 *  ``Set`` iteration order is insertion order, not numeric order, and
 *  the render should always walk the stages low-to-high regardless of
 *  click order. The card fields travel only when a card is on: every
 *  default equals the server's own, so an untouched panel leaves the
 *  body exactly as it was before the cards existed (#973). */
export function buildCompareGridPayload(input: {
  stageNumbers: number[];
  audioFrom: string;
  canvas: CanvasChoice;
  outputName: string;
  render?: RenderOptions;
  /** #705: the per-tile overlay and, with it on, the summary hold. The
   *  server refuses a hold without the overlay, so the hold travels
   *  only alongside it. */
  overlay?: boolean;
  summaryHoldSeconds?: number;
}): CompareGridRequestPayload {
  const payload: CompareGridRequestPayload = {
    stage_numbers: [...input.stageNumbers].sort((a, b) => a - b),
    audio_from: input.audioFrom,
    canvas_width: input.canvas.width,
    canvas_height: input.canvas.height,
    output_name: input.outputName,
  };
  if (input.render && anyRenderOptionOn(input.render)) Object.assign(payload, gridExportFields(input.render));
  if (input.overlay) {
    payload.overlay = true;
    const hold = input.summaryHoldSeconds ?? 0;
    if (Number.isFinite(hold) && hold > 0) payload.summary_hold_seconds = Math.min(30, hold);
  }
  return payload;
}

export interface GridResultSummary {
  headline: string;
  partial: boolean;
  failedStages: string[];
  /** Selected stages that never got planned, because no shooter had a
   *  trim for them. Not failures -- ffmpeg was never asked. */
  skippedStages: number[];
  /** One line per shooter/stage pair whose cell rendered black. */
  missingTrims: string[];
}

/** Turn a finished job's ``CompareGridResult`` into display copy.
 *
 *  A partial render (some stages failed, others didn't) is a success
 *  with a warning, never a failure -- the headline always leads with
 *  what rendered, and the rest of the fields carry the warnings so the
 *  page can show both the output and the banner.
 *
 *  "Partial" is wider than ``failed``. A stage nobody had a trim for is
 *  dropped before ffmpeg is invoked, so it appears in neither
 *  ``failed`` nor ``stages_rendered``; counted against
 *  ``stages_total`` (which is what the request asked for) it shows up
 *  as a shortfall, and reporting "Rendered all 2 stages" for a
 *  three-stage request is exactly the silent loss this guards. A
 *  missing trim on a stage that *did* render is a warning too: the
 *  cell comes out black, which is indistinguishable from a shooter who
 *  skipped the stage unless we say so (#618). */
export function summarizeGridResult(result: CompareGridResult): GridResultSummary {
  const skippedStages = result.skipped_stages ?? [];
  const missingTrims = (result.missing_trims ?? []).map(
    (m) => `${m.shooter} has no trim for stage ${m.stage_number} (${m.stage_name})`,
  );
  const short = result.stages_rendered < result.stages_total;
  const partial =
    result.failed.length > 0 || skippedStages.length > 0 || missingTrims.length > 0 || short;
  const failedStages = result.failed.map((f) => f.stage_name);
  const headline =
    result.failed.length > 0 || skippedStages.length > 0 || short
      ? `Rendered ${result.stages_rendered} of ${result.stages_total} stages`
      : `Rendered all ${result.stages_rendered} stages`;
  return { headline, partial, failedStages, skippedStages, missingTrims };
}
