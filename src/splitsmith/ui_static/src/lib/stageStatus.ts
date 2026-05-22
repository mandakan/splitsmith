/**
 * Per-stage lifecycle helpers.
 *
 * The backend (``splitsmith.ui.project.stage_audit_status``) is the single
 * source of truth for whether a stage is audited / ready / in progress /
 * etc. This module exists for two things and two things only:
 *
 *   1. Display mapping -- given a :type:`StageStatus`, return the
 *      tone/label/glyph the SPA renders for it. The sidebar, Home
 *      overview cards, and shooter chip strip all read these so the
 *      visual language stays consistent.
 *
 *   2. Fallback derivation -- legacy responses (pre-status-enrichment)
 *      or partial payloads can be missing ``status``. We derive a best-
 *      effort value from the locally-available fields, which keeps old
 *      clients alive while the backend rolls out.
 *
 * No classifier logic lives here beyond the fallback. If you find
 * yourself adding "if stage.time_seconds > 0 && hasVideo" to compute
 * audited state, stop -- that calculation belongs in the backend.
 */

import type { StageEntry, StageStatus } from "@/lib/api";

/** Visual tone the existing sidebar / chip rail / home card components
 *  speak. Maps StageStatus -> tone so display code stays declarative. */
export type StageStatusTone = "done" | "in_progress" | "ready" | "partial" | "todo" | "skipped";

/** Best-effort fallback when the backend payload predates the status
 *  enrichment. Mirrors :func:`stage_audit_status` for the cases we can
 *  determine without audit-JSON access -- everything past ``ready``
 *  falls back to ``ready`` since the client can't see audit events. */
export function deriveStageStatus(stage: StageEntry): StageStatus {
  if (stage.status) return stage.status;
  if (stage.skipped) return "skipped";
  const hasPrimary = (stage.videos ?? []).some((v) => v.role === "primary");
  if (!hasPrimary) return "todo";
  if (stage.time_seconds <= 0) return "partial";
  return "ready";
}

/** Map a status to a display tone. The tone is what the UI components
 *  switch on -- colors, icons, chip styling. Two statuses can share a
 *  tone when they visually collapse (e.g. ``audited`` and ``skipped``
 *  are both "done" because they're both terminal). */
export function statusTone(status: StageStatus): StageStatusTone {
  switch (status) {
    case "audited":
      return "done";
    case "skipped":
      return "skipped";
    case "in_progress":
      return "in_progress";
    case "ready":
      return "ready";
    case "partial":
      return "partial";
    case "todo":
      return "todo";
  }
}

/** Short human label for a status. Used by the home card chip + the
 *  per-stage detail row. Kept here so all surfaces agree on the noun:
 *  "audited" used to be misapplied to "ready" stages; with this in one
 *  place the lie can't sneak back in. */
export function statusLabel(status: StageStatus): string {
  switch (status) {
    case "audited":
      return "Audited";
    case "skipped":
      return "Skipped";
    case "in_progress":
      return "In progress";
    case "ready":
      return "Ready";
    case "partial":
      return "Stage time missing";
    case "todo":
      return "Awaiting footage";
  }
}

/** ``true`` when the stage represents work the operator has finished --
 *  audited or explicitly skipped. The match-overview progress bar /
 *  "N of 12 done" counter uses this so skipped stages still count as
 *  closed out (the operator decided not to audit them, which is a
 *  decision, not pending work). */
export function isTerminal(status: StageStatus): boolean {
  return status === "audited" || status === "skipped";
}

/** ``true`` when the stage is the natural "next up" candidate the
 *  sidebar / home card highlights. Anything not yet audited and not
 *  skipped is fair game; the caller picks the FIRST such stage. */
export function isNextUpCandidate(status: StageStatus): boolean {
  return status !== "audited" && status !== "skipped";
}
