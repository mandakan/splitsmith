/**
 * Pure builders for the mobile interval-reclassify write path.
 *
 * buildCoachPatch turns the sheet's draft into the minimal
 * CoachShotPatch (null when nothing changed - the caller just closes
 * the sheet). buildUndoPatch inverts exactly the fields a patch
 * touched: a prior manual class is restored verbatim; a prior auto (or
 * absent) class is reverted with clear_class so the server re-derives
 * the rule verdict instead of us faking an "auto" write client-side.
 */
import type { CoachIntervalClass, CoachShot, CoachShotPatch } from "@/lib/api";

export interface ReclassifyDraft {
  /** Selected class; null means the sheet never had a selection. */
  intervalClass: CoachIntervalClass | null;
  /** Note textarea contents, untrimmed. */
  note: string;
}

export function buildCoachPatch(prev: CoachShot, draft: ReclassifyDraft): CoachShotPatch | null {
  const patch: CoachShotPatch = {};
  if (draft.intervalClass != null && draft.intervalClass !== prev.interval_class) {
    patch.interval_class = draft.intervalClass;
    patch.interval_class_source = "manual";
  }
  const note = draft.note.trim();
  const prevNote = prev.coaching_note ?? "";
  if (note !== prevNote) {
    if (note === "") patch.clear_note = true;
    else patch.coaching_note = note;
  }
  return Object.keys(patch).length > 0 ? patch : null;
}

export function buildUndoPatch(prev: CoachShot, applied: CoachShotPatch): CoachShotPatch {
  const undo: CoachShotPatch = {};
  if (applied.interval_class !== undefined || applied.clear_class) {
    if (prev.interval_class != null && prev.interval_class_source === "manual") {
      undo.interval_class = prev.interval_class;
      undo.interval_class_source = "manual";
    } else {
      undo.clear_class = true;
    }
  }
  if (applied.coaching_note !== undefined || applied.clear_note) {
    if (prev.coaching_note != null && prev.coaching_note !== "") {
      undo.coaching_note = prev.coaching_note;
    } else {
      undo.clear_note = true;
    }
  }
  return undo;
}
