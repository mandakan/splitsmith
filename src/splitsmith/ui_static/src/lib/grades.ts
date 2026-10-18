/**
 * Colour grades on the Export page (``splitsmith.grade``): which tiles the
 * Grade row offers and what a choice sends. A grade is ``look`` (the Look's
 * own, the default; none for a Look without one), ``none``, ``preset:<id>``
 * or ``user:<file>`` (your own LUT, desktop only), and a strength (``null`` is
 * the grade's own: the Look's, else full).
 */
import type { GradeInfo, LookInfo, MatchExportRequestPayload } from "./api";

export const DEFAULT_GRADE = "look";

export interface GradeChoice {
  choice: string;
  strength: number | null;
}

export interface GradeTile {
  id: string;
  label: string;
  preview: string;
}

function previewUrl(choice: string, look: string): string {
  const params = new URLSearchParams({ grade: choice });
  if (choice === "look") params.set("look", look);
  return `/api/grades/preview?${params.toString()}`;
}

/** The Grade row's tiles, in order: None, the chosen Look's own grade when
 *  it has one, the presets, then your own LUTs. */
export function gradeTiles(
  looks: readonly LookInfo[],
  look: string,
  presets: readonly GradeInfo[],
  user: readonly GradeInfo[],
): GradeTile[] {
  const tiles: GradeTile[] = [{ id: "none", label: "None", preview: previewUrl("none", look) }];
  const info = looks.find((l) => l.name === look);
  if (info?.grade) tiles.push({ id: "look", label: `${info.label || info.name}'s grade`, preview: previewUrl("look", look) });
  return [...tiles, ...presets, ...user];
}

/** The tile a stored choice shows as selected. ``look`` on a Look without a
 *  grade is no grade at all, and a choice the gallery no longer lists (a LUT
 *  deleted, another machine's) reads as the Look's default. */
export function selectedTile(tiles: readonly GradeTile[], choice: string): string {
  if (tiles.some((t) => t.id === choice)) return choice;
  return tiles.some((t) => t.id === "look") ? "look" : "none";
}

/** Whether the chosen tile grades anything (the strength slider shows). */
export function grades(selected: string): boolean {
  return selected !== "none";
}

/** The request fields for a rendered MP4: sent only when not the defaults,
 *  so an untouched form sends the body it always sent. A choice the gallery
 *  does not list is sent as the default (``look``), never as a 400. */
export function gradeFields(
  grade: GradeChoice | undefined,
  outputFormat: string,
  tiles?: readonly GradeTile[],
): Pick<MatchExportRequestPayload, "grade" | "grade_strength"> {
  if (!grade || outputFormat !== "mp4") return {};
  // A choice the gallery does not list goes out as the default, never as a 400.
  const choice = tiles && !tiles.some((t) => t.id === grade.choice) ? DEFAULT_GRADE : grade.choice;
  const out: Pick<MatchExportRequestPayload, "grade" | "grade_strength"> = {};
  if (choice !== DEFAULT_GRADE) out.grade = choice;
  if (grade.strength !== null && choice !== "none") out.grade_strength = Math.min(1, Math.max(0, grade.strength));
  return out;
}

/** The rail and the group summary's word for the grade. */
export function gradeLabel(tiles: readonly GradeTile[], choice: string): string {
  return tiles.find((t) => t.id === selectedTile(tiles, choice))?.label ?? "None";
}
