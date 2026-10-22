/**
 * Stage details (spec 2026-10-22): the page's rows, the pasted results
 * table, and the scoring rules the page previews before Save. The server
 * applies the same rules on save (``stage_details.py``); the numbers in
 * the tests here are the ones its tests pin.
 */
import type { ShooterListEntry, StageDetailsRequest, StageDetailsView } from "@/lib/api";

export type PowerFactor = "minor" | "major";

export const HIT_FIELDS = ["alphas", "charlies", "deltas", "misses", "no_shoots", "procedurals"] as const;
export type HitField = (typeof HIT_FIELDS)[number];
export const NUM_SCORE_FIELDS = [...HIT_FIELDS, "stage_points", "hit_factor", "stage_pct"] as const;
export type NumScoreField = (typeof NUM_SCORE_FIELDS)[number];
/** Every cell of a row the user types into, as typed. */
export type CellField = "time_seconds" | "shot_order" | NumScoreField;

export interface EditRow {
  stage_number: number;
  stage_name: string;
  skipped: boolean;
  cells: Record<CellField, string>;
  dq: boolean | null;
  /** Score fields the user typed or pasted (sent as ``manual``). */
  manual: Set<NumScoreField | "dq">;
  /** Fields the server derived last time: they show dashed and may change. */
  derived: Set<string>;
  /** Cells filled by the last paste and not saved yet. */
  pasted: Set<CellField>;
}

const VALUES: Record<PowerFactor, Record<HitField, number>> = {
  minor: { alphas: 5, charlies: 3, deltas: 1, misses: -10, no_shoots: -10, procedurals: -10 },
  major: { alphas: 5, charlies: 4, deltas: 2, misses: -10, no_shoots: -10, procedurals: -10 },
};

/** A typed number, decimal comma or point; null when empty or not a number. */
export function parseNumber(text: string): number | null {
  const t = text.trim().replace(",", ".");
  if (t === "" || t === "-") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function show(n: number | null | undefined): string {
  return n === null || n === undefined ? "" : String(n);
}

export function rowsFromView(view: StageDetailsView): EditRow[] {
  return view.stages.map((s) => {
    const card = s.scorecard;
    const cells = {
      time_seconds: show(s.time_seconds),
      shot_order: show(s.shot_order),
    } as Record<CellField, string>;
    for (const f of NUM_SCORE_FIELDS) cells[f] = card ? show(card[f]) : "";
    return {
      stage_number: s.stage_number,
      stage_name: s.stage_name,
      skipped: s.skipped,
      cells,
      dq: card?.dq ?? null,
      manual: new Set((card?.manual ?? []) as (NumScoreField | "dq")[]),
      derived: new Set(card?.derived ?? []),
      pasted: new Set(),
    };
  });
}

export function pointsFromHits(row: EditRow, pf: PowerFactor): number | null {
  let total = 0;
  for (const f of HIT_FIELDS) {
    const n = parseNumber(row.cells[f]);
    if (n === null) return null;
    // Counts are whole numbers; the save rounds them the same way.
    total += VALUES[pf][f] * Math.round(n);
  }
  return Math.max(total, 0);
}

export function hitFactor(points: number | null, time: number | null): number | null {
  if (points === null || time === null || time <= 0) return null;
  return Math.round((points / time) * 10000) / 10000;
}

/** A field the page may fill itself: empty, or filled by the derivation
 *  before and not typed since. */
function derivable(row: EditRow, field: "stage_points" | "hit_factor"): boolean {
  if (row.manual.has(field)) return false;
  return row.cells[field].trim() === "" || row.derived.has(field);
}

export interface Shown {
  value: string;
  derived: boolean;
}

/** Points and hit factor as the page shows them: typed or stored values
 *  as they are, otherwise worked out (dashed). */
export function shownScores(row: EditRow, pf: PowerFactor): { points: Shown; hf: Shown } {
  const pointsDerived = derivable(row, "stage_points");
  const points = pointsDerived ? pointsFromHits(row, pf) : parseNumber(row.cells.stage_points);
  const pointsShown = pointsDerived
    ? { value: show(points), derived: points !== null }
    : { value: row.cells.stage_points, derived: false };
  const hfDerived = derivable(row, "hit_factor");
  const hf = hfDerived ? hitFactor(points, parseNumber(row.cells.time_seconds)) : null;
  const hfShown = hfDerived
    ? { value: show(hf), derived: hf !== null }
    : { value: row.cells.hit_factor, derived: false };
  return { points: pointsShown, hf: hfShown };
}

const HIT_LABEL: Record<HitField, string> = {
  alphas: "A",
  charlies: "C",
  deltas: "D",
  misses: "M",
  no_shoots: "NS",
  procedurals: "Proc",
};

function joinWords(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** A warning when entered points disagree with the hits; the entered
 *  value is kept. Null when they agree or cannot be compared. */
export function disagreement(row: EditRow, pf: PowerFactor): string | null {
  if (derivable(row, "stage_points")) return null;
  const entered = parseNumber(row.cells.stage_points);
  const computed = pointsFromHits(row, pf);
  if (entered === null || computed === null || entered === computed) return null;
  const hits = HIT_FIELDS.filter((f) => (parseNumber(row.cells[f]) ?? 0) !== 0).map(
    (f) => `${parseNumber(row.cells[f])} ${HIT_LABEL[f]}`,
  );
  return `${joinWords(hits) || "No hits"} give ${computed} points, the entered value is ${entered}. Kept as entered.`;
}

// --- paste --------------------------------------------------------------

const HEADERS: Record<string, CellField | "stage" | null> = {
  station: "stage",
  stage: "stage",
  stations: "stage",
  hf: "hit_factor",
  tid: "time_seconds",
  time: "time_seconds",
  pts: "stage_points",
  points: "stage_points",
  a: "alphas",
  c: "charlies",
  d: "deltas",
  m: "misses",
  proc: "procedurals",
  ns: "no_shoots",
};

export interface PastedRow {
  stage_number: number;
  values: Partial<Record<CellField, string>>;
}

export interface PasteResult {
  rows: PastedRow[];
  /** Lines that looked like data but could not be placed on a stage. */
  unused: string[];
  /** False when no header row with a stage column was found. */
  understood: boolean;
}

function splitLine(line: string): string[] {
  const cells = line.includes("\t") ? line.split("\t") : line.split(/ {2,}/);
  return cells.map((c) => c.trim());
}

/** The results table as a browser copies it from Shoot'n Score It:
 *  one header row, then a row per stage, tab separated. */
export function parsePastedResults(text: string, stageNumbers: readonly number[]): PasteResult {
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.replace(/\s+$/, ""))
    .filter((l) => l.trim() !== "");
  const headerAt = lines.findIndex((l) =>
    splitLine(l).some((c) => HEADERS[c.toLowerCase()] === "stage"),
  );
  if (headerAt < 0) return { rows: [], unused: [], understood: false };
  const columns = splitLine(lines[headerAt]).map((c) => HEADERS[c.toLowerCase()] ?? null);
  const stageCol = columns.indexOf("stage");
  const known = new Set(stageNumbers);
  const rows: PastedRow[] = [];
  const unused: string[] = [];
  for (const line of lines.slice(headerAt + 1)) {
    const cells = splitLine(line);
    const label = cells[stageCol] ?? "";
    const m = /^\s*(\d+)/.exec(label);
    const n = m ? Number(m[1]) : null;
    if (n === null || !known.has(n)) {
      unused.push(label || line.trim());
      continue;
    }
    const values: Partial<Record<CellField, string>> = {};
    columns.forEach((col, i) => {
      if (col === null || col === "stage" || cells[i] === undefined || cells[i] === "") return;
      if (parseNumber(cells[i]) === null) return;
      values[col] = String(parseNumber(cells[i]));
    });
    rows.push({ stage_number: n, values });
  }
  return { rows, unused, understood: true };
}

/** ``rows`` with the pasted values filled in, unsaved. */
export function applyPaste(rows: EditRow[], pasted: PastedRow[]): EditRow[] {
  const by = new Map(pasted.map((p) => [p.stage_number, p]));
  return rows.map((row) => {
    const p = by.get(row.stage_number);
    if (!p) return row;
    const next = withCells(row, p.values);
    next.pasted = new Set(Object.keys(p.values) as CellField[]);
    return next;
  });
}

/** ``row`` with ``values`` typed into it: score fields become manual. */
export function withCells(row: EditRow, values: Partial<Record<CellField, string>>): EditRow {
  const cells = { ...row.cells, ...values };
  const manual = new Set(row.manual);
  const derived = new Set(row.derived);
  for (const k of Object.keys(values) as CellField[]) {
    if (k === "time_seconds" || k === "shot_order") continue;
    derived.delete(k);
    // An emptied cell is not typed any more: the page works it out again
    // and a sync may fill it.
    if ((values[k] ?? "").trim() === "") manual.delete(k);
    else manual.add(k);
  }
  return { ...row, cells, manual, derived, pasted: new Set(row.pasted) };
}

// --- order --------------------------------------------------------------

/** ``rows`` numbered in ``order`` (stage numbers, first shot first);
 *  stages not in it lose their number. */
export function copyOrder(rows: EditRow[], order: readonly number[]): EditRow[] {
  const pos = new Map(order.map((n, i) => [n, i + 1]));
  return rows.map((r) => ({ ...r, cells: { ...r.cells, shot_order: show(pos.get(r.stage_number)) } }));
}

/** Stage numbers whose order collides with another row's. */
export function duplicateOrders(rows: EditRow[]): Set<number> {
  const seen = new Map<number, number[]>();
  for (const r of rows) {
    const n = parseNumber(r.cells.shot_order);
    if (n === null) continue;
    seen.set(n, [...(seen.get(n) ?? []), r.stage_number]);
  }
  return new Set([...seen.values()].filter((s) => s.length > 1).flat());
}

/** Rows moved: ``from`` placed before ``to`` in shooting order, and every
 *  row numbered by its new position (drag and drop). */
export function moveInOrder(rows: EditRow[], from: number, to: number): EditRow[] {
  const ordered = [...rows].sort((a, b) => orderKey(a) - orderKey(b));
  const moving = ordered.find((r) => r.stage_number === from);
  if (!moving) return rows;
  const rest = ordered.filter((r) => r.stage_number !== from);
  const at = rest.findIndex((r) => r.stage_number === to);
  rest.splice(at < 0 ? rest.length : at, 0, moving);
  return copyOrder(
    rows,
    rest.map((r) => r.stage_number),
  );
}

function orderKey(r: EditRow): number {
  return parseNumber(r.cells.shot_order) ?? 1000 + r.stage_number;
}

export function toRequest(rows: EditRow[], powerFactor: PowerFactor | null): StageDetailsRequest {
  return {
    power_factor: powerFactor,
    rows: rows.map((r) => {
      const ints = (f: CellField) => {
        const n = parseNumber(r.cells[f]);
        return n === null ? null : Math.round(n);
      };
      return {
        stage_number: r.stage_number,
        time_seconds: parseNumber(r.cells.time_seconds),
        shot_order: ints("shot_order"),
        alphas: ints("alphas"),
        charlies: ints("charlies"),
        deltas: ints("deltas"),
        misses: ints("misses"),
        no_shoots: ints("no_shoots"),
        procedurals: ints("procedurals"),
        stage_points: r.manual.has("stage_points") ? parseNumber(r.cells.stage_points) : null,
        hit_factor: r.manual.has("hit_factor") ? parseNumber(r.cells.hit_factor) : null,
        stage_pct: parseNumber(r.cells.stage_pct),
        dq: r.dq,
        manual: [...r.manual],
      };
    }),
  };
}

/** Rows with a time: the page's "N of M have a time" line. */
export function timedCount(rows: EditRow[]): number {
  return rows.filter((r) => !r.skipped && (parseNumber(r.cells.time_seconds) ?? 0) > 0).length;
}

// --- the Overview notice ------------------------------------------------

export interface DetailsNotice {
  shooters: { slug: string; name: string }[];
  title: string;
}

/** The Overview's notice: every shooter with a stage still lacking a time. */
export function detailsNotice(shooters: readonly ShooterListEntry[]): DetailsNotice | null {
  const missing = shooters.filter((s) => (s.stages_without_time ?? 0) > 0);
  if (missing.length === 0) return null;
  const names = joinWords(missing.map((s) => firstName(s.name)));
  const verb = missing.length === 1 ? "has" : "have";
  return {
    shooters: missing.map((s) => ({ slug: s.slug, name: firstName(s.name) })),
    title: `${names} ${verb} stages without a time`,
  };
}

export function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] || name;
}
