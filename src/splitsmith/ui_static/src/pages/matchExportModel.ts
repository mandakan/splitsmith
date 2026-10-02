/**
 * Plain logic behind the match-scoped compare-grid export page
 * (``MatchExport.tsx``, phase 0). Split out from the component so both
 * it and its test can build the request payload / summarise the job
 * result without going through React.
 */

import type { CompareGridRequestPayload, CompareGridResult, MatchExportRequestPayload, OverlayCodec } from "@/lib/api";
import { camExportFields, type CamOptions } from "@/lib/camOptions";
import type { TransitionKind } from "@/lib/exportPresets";
import {
  anyRenderOptionOn,
  clampSeconds,
  gridExportFields,
  matchExportFields,
  transitionsSupported,
  type OutputFormat,
  type RenderOptions,
} from "@/lib/renderOptions";
import { rowUploadOptions, type UploadFormOptions } from "@/lib/youtubeRows";

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
  /** The YouTube sidecar and the chained upload, as for one shooter. */
  youtube?: boolean;
  descriptionLead?: string;
  uploadOptions?: UploadFormOptions;
  youtubeConnected?: boolean;
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
  if (input.youtube) {
    payload.youtube_sidecar = true;
    payload.description_lead = input.descriptionLead?.trim() || null;
    if (input.uploadOptions?.enabled && input.youtubeConnected) {
      const upload = rowUploadOptions(input.uploadOptions);
      Object.assign(payload, {
        youtube_upload: true,
        youtube_privacy: upload.privacy,
        youtube_playlist: upload.playlist,
        youtube_playlist_id: upload.playlist_id,
        youtube_publish_at: upload.publish_at,
        youtube_notify_subscribers: upload.notify_subscribers,
      });
    }
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

/** Input to {@link buildMatchExportPayload}: exactly what the Export
 *  page's single-shooter bundle form (submitBundle) reads, plus two
 *  fields that only exist because of the desktop-render wrapper (#1100). */
export interface MatchExportPayloadInput {
  stageNumbers: number[];
  headPad: number;
  tailPad: number;
  camOptions: CamOptions;
  outputFormat: OutputFormat;
  transitionKind: TransitionKind;
  transitionSeconds: number;
  renderOptions: RenderOptions;
  youtube: boolean;
  descriptionLead: string;
  uploadOptions: UploadFormOptions;
  includeOverlay: boolean;
  overlayCodec: OverlayCodec;
  projectName: string;
  /** "desk" posts an export here; "desktop" asks the linked desktop to
   *  render and upload, which only makes sense as an MP4 that uploads. */
  uploadTarget: "desk" | "desktop";
  youtubeConnected: boolean;
}

/** The single-shooter match-export request body, for either the desk
 *  (``POST /api/shooters/{slug}/export/match``) or a desktop render
 *  request (``POST /api/match/desktop-commands``). A desktop render
 *  forces an MP4 output that uploads -- there is no other reason to ask
 *  the desktop to render. Everything else is unchanged from the literal
 *  Export.tsx's ``submitBundle`` used to build inline. */
export function buildMatchExportPayload(input: MatchExportPayloadInput): MatchExportRequestPayload {
  const desktop = input.uploadTarget === "desktop";
  const outputFormat = desktop ? "mp4" : input.outputFormat;
  const renderedMp4 = outputFormat === "mp4";
  const youtube = desktop || (renderedMp4 && input.youtube);
  const upload = rowUploadOptions(input.uploadOptions);
  return {
    stage_numbers: input.stageNumbers,
    head_pad_seconds: input.headPad,
    tail_pad_seconds: input.tailPad,
    ...camExportFields(input.camOptions),
    output_format: outputFormat,
    transition_kind: transitionsSupported(outputFormat) ? input.transitionKind : "none",
    transition_duration_seconds: clampSeconds(input.transitionSeconds, 0.1),
    ...matchExportFields(input.renderOptions, outputFormat),
    intro_path: undefined,
    outro_path: undefined,
    youtube_sidecar: youtube,
    description_lead: youtube ? input.descriptionLead.trim() || null : undefined,
    youtube_preset: youtube,
    youtube_upload: desktop || (youtube && input.youtubeConnected && input.uploadOptions.enabled),
    youtube_privacy: upload.privacy,
    youtube_playlist: upload.playlist,
    // The hosted picker lists the hosted connection's playlists, which may
    // be another channel than the desktop's; by title only there.
    youtube_playlist_id: desktop ? null : upload.playlist_id,
    youtube_publish_at: upload.publish_at,
    youtube_notify_subscribers: upload.notify_subscribers,
    include_overlay: input.includeOverlay,
    overlay_codec: input.overlayCodec,
    overlay_max_height: null,
    overlay_max_fps: null,
    project_name: input.projectName,
  };
}
