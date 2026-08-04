/**
 * Plain logic behind the match-scoped compare-grid export page
 * (``MatchExport.tsx``, phase 0). Split out from the component so both
 * it and its test can build the request payload / summarise the job
 * result without going through React.
 */

import type { CompareGridRequestPayload, CompareGridResult } from "@/lib/api";

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
 *  click order. */
export function buildCompareGridPayload(input: {
  stageNumbers: number[];
  audioFrom: string;
  canvas: CanvasChoice;
  outputName: string;
}): CompareGridRequestPayload {
  return {
    stage_numbers: [...input.stageNumbers].sort((a, b) => a - b),
    audio_from: input.audioFrom,
    canvas_width: input.canvas.width,
    canvas_height: input.canvas.height,
    output_name: input.outputName,
  };
}

export interface GridResultSummary {
  headline: string;
  partial: boolean;
  failedStages: string[];
}

/** Turn a finished job's ``CompareGridResult`` into display copy.
 *
 *  A partial render (some stages failed, others didn't) is a success
 *  with a warning, never a failure -- the headline always leads with
 *  what rendered, and ``partial``/``failedStages`` carry the rest so
 *  the page can show both the output and the warning banner. */
export function summarizeGridResult(result: CompareGridResult): GridResultSummary {
  const partial = result.failed.length > 0;
  const failedStages = result.failed.map((f) => f.stage_name);
  const headline = partial
    ? `Rendered ${result.stages_rendered} of ${result.stages_total} stages`
    : `Rendered all ${result.stages_rendered} stages`;
  return { headline, partial, failedStages };
}
