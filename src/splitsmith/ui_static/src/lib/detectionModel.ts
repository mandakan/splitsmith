/**
 * Wording for the detection model choice (spec 2026-10-26): the Overview
 * card's status line and the Audit header's label for the model a stage was
 * detected with. ``base`` needs no download; ``clap`` adds the CLAP audio
 * model, which the UI calls "the audio model".
 */
import type { DetectionModelSettings, StageAudit } from "./api";

/** The card's status line under its switch; ``null`` when there is nothing to say. */
export function detectionModelLine(settings: DetectionModelSettings | null): string | null {
  if (settings == null || settings.required.length === 0) return null;
  if (settings.active_job != null) return "Downloading...";
  if (settings.present) return "Ready";
  return "Not downloaded yet. Detection runs without it until it is.";
}

/** The Audit header's label, or ``null`` for a detection made before model
 *  sets existed (its model no longer ships, so it is never called base). */
export function detectedWithLabel(block: StageAudit["_candidates_pending_audit"]): string | null {
  switch (block?._model_set) {
    case "clap":
      return "Detected with the audio model";
    case "base":
      return "Detected without the audio model";
    default:
      return null;
  }
}
