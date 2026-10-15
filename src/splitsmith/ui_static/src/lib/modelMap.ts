/**
 * The lab's Models page (``GET /api/dev/models``, ``lab/model_map.py``):
 * the payload's types and every derivation the page draws from it. The
 * components map these results to SVG and primitives; nothing here
 * touches the DOM.
 */

export type CameraClass = "headcam" | "handheld";
export type HeldOut = "right" | "wrong" | "unreachable";

export interface BeepRankerUse {
  tags: string[];
  camera_kind: string | null;
  held_out: HeldOut | null;
}

export interface ModelMapFixture {
  stem: string;
  match: string | null;
  stage: number | null;
  shooter_id: string | null;
  camera_class: CameraClass;
  camera: string | null;
  n_shots: number;
  expected_rounds: number | null;
  has_wav: boolean;
  review_status: string;
  superseded_by: string | null;
  wrong_clip: boolean;
  voter_c: CameraClass | null;
  match_group: string | null;
  stage_group: string | null;
  beep_ranker: BeepRankerUse | null;
  /** This model's voter C on the fixture while its match was left out. */
  heldout_lomo?: { tp: number; fp: number; fn: number } | null;
}

export interface ModelSummary {
  id: string;
  name: string;
  version: string;
  channel: string;
  built_at: string | null;
  description: string | null;
  n_fixtures: Record<string, number>;
  selection: string | null;
}

export interface SplitMetrics {
  precision: number;
  recall: number;
  f1: number;
  tp: number;
  fp: number;
  fn: number;
}

export interface HeldoutSplit {
  split: string;
  at_shipped_threshold: SplitMetrics;
  own_threshold: number | null;
  at_own_threshold: SplitMetrics | null;
}

export interface VoterCHead {
  camera_class: CameraClass;
  n_fixtures: number;
  n_candidates: number;
  n_positives: number;
  voter_a_floor: number | null;
  voter_b_threshold: number | null;
  voter_c_threshold: number | null;
  cv: SplitMetrics | null;
  onnx: string | null;
  onnx_bytes: number | null;
  heldout: HeldoutSplit[];
  heldout_matches: string[];
  heldout_stages: number | null;
  heldout_candidates: number | null;
  lomo_by_match?: Record<string, SplitMetrics>;
}

export interface ModelArtifact {
  name: string;
  filename: string;
  sha256: string;
  size_bytes: number;
}

export interface EnsembleModel {
  built_at: string | null;
  target_recall: number | null;
  tolerance_ms: number | null;
  feature_dim: number | null;
  default_camera_class: string | null;
  heads: VoterCHead[];
  hyperparameters: Record<string, unknown>;
  splits: Record<string, string>;
  heldout_built_at: string | null;
  clap_prompts: string[];
  clap_shot_prompts: string[];
  amp_floor_by_camera_model: Record<string, number>;
  artifacts: ModelArtifact[];
}

export interface RankerFeature {
  name: string;
  lr_coef: number | null;
  gbdt_importance: number | null;
}

export interface RankerScore {
  name: string;
  n: number;
  top1: number;
  top1_pct: number;
}

export interface BeepRankerModel {
  shipped_version: string | null;
  shipped_kind: string;
  winner: string | null;
  n_fixtures: number;
  n_reachable: number;
  folds: string[];
  features: RankerFeature[];
  scores: RankerScore[];
  by_tag: Record<string, RankerScore>;
}

export interface ModelVersion {
  sha: string;
  date: string;
  subject: string;
  built_at: string | null;
  n_fixtures: Record<string, number>;
  recall: Record<string, number>;
  precision: Record<string, number>;
  added: string[];
  removed: string[];
}

export interface ModelMap {
  fixtures: ModelMapFixture[];
  ensemble: EnsembleModel;
  beep_ranker: BeepRankerModel | null;
  history: ModelVersion[];
  model?: ModelSummary | null;
  models?: ModelSummary[];
}

// -- status ------------------------------------------------------------------

/**
 * Where a fixture stands against the shipped build. What the build used
 * comes first: a fixture that calibrated a head is that head, whatever has
 * happened to it since (``fixtureFlags`` says what). The rest are not in
 * the build: replaced by a newer audit, waiting for a review, or simply
 * added since it was built.
 */
export type FixtureStatus = "headcam" | "handheld" | "review" | "unused" | "superseded";

export function fixtureStatus(f: ModelMapFixture): FixtureStatus {
  if (f.voter_c) return f.voter_c;
  if (f.superseded_by) return "superseded";
  if (f.review_status === "needs_review") return "review";
  return "unused";
}

/**
 * What will change for a fixture the shipped build used: ``leaving``, a
 * newer audit replaces it at the next retrain; ``unreviewed``, its times
 * still need a review (the build trained on it anyway).
 */
export function fixtureFlags(f: ModelMapFixture): { leaving: boolean; unreviewed: boolean } {
  return {
    leaving: f.voter_c !== null && f.superseded_by !== null,
    unreviewed: f.voter_c !== null && f.superseded_by === null && f.review_status === "needs_review",
  };
}

/**
 * Voter C is two classifiers, trained apart: one on head-mounted cameras,
 * one on everything else (phones and action cameras held or on a stand).
 * The build calls them heads; the page says classifier, since "headcam
 * head" reads as a body part.
 */
export const CLASSIFIER_LABEL: Record<CameraClass, string> = {
  headcam: "Headcam classifier",
  handheld: "Handheld classifier",
};

export const CLASSIFIER_EXPLAIN =
  "Shot classification (voter C) is two classifiers trained apart: one on head-mounted cameras, one on handheld cameras (phones and action cameras held or on a stand). Each fixture trains the one for its camera.";

export const STATUS_LABEL: Record<FixtureStatus, string> = {
  headcam: "Headcam classifier",
  handheld: "Handheld classifier",
  review: "Awaiting review",
  unused: "Not in build",
  superseded: "Superseded",
};

export const STATUS_ORDER: FixtureStatus[] = ["headcam", "handheld", "review", "unused", "superseded"];

/** One hue per status; ``unused`` and ``superseded`` draw hollow. */
export const STATUS_COLOR: Record<FixtureStatus, string> = {
  headcam: "var(--color-beep)",
  handheld: "var(--color-manual)",
  review: "var(--color-live)",
  unused: "var(--color-muted)",
  superseded: "var(--color-whisper)",
};

export const HELD_OUT_COLOR: Record<HeldOut | "none", string> = {
  right: "var(--color-done)",
  wrong: "var(--color-live)",
  unreachable: "var(--color-led-soft)",
  none: "var(--color-whisper)",
};

/** Categorical hues for match folds: distinct, none of them the brand red. */
export const FOLD_PALETTE = [
  "#06B6D4",
  "#A78BFA",
  "#4ADE80",
  "#FBBF24",
  "#F472B6",
  "#60A5FA",
  "#FB923C",
  "#2DD4BF",
  "#C084FC",
  "#A3E635",
  "#94A3B8",
  "#FDE68A",
];

/** The event a fixture belongs to: the build's fold key, else the stem's. */
export function matchKey(f: ModelMapFixture): string {
  return f.match_group ?? f.match ?? "unknown";
}

export function foldColors(matches: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  [...matches].sort().forEach((m, i) => {
    out[m] = FOLD_PALETTE[i % FOLD_PALETTE.length];
  });
  return out;
}

// -- filtering and grouping --------------------------------------------------

export interface MapFilter {
  match: string | null;
  cameraClass: CameraClass | null;
  status: FixtureStatus | null;
  text: string;
}

export const NO_FILTER: MapFilter = { match: null, cameraClass: null, status: null, text: "" };

export function filterFixtures(fixtures: ModelMapFixture[], filter: MapFilter): ModelMapFixture[] {
  const text = filter.text.trim().toLowerCase();
  return fixtures.filter(
    (f) =>
      (filter.match === null || matchKey(f) === filter.match) &&
      (filter.cameraClass === null || f.camera_class === filter.cameraClass) &&
      (filter.status === null || fixtureStatus(f) === filter.status) &&
      (text === "" || f.stem.toLowerCase().includes(text) || (f.camera ?? "").toLowerCase().includes(text)),
  );
}

export interface StageBlock {
  stage: number | null;
  fixtures: ModelMapFixture[];
}

export interface MatchRow {
  match: string;
  stages: StageBlock[];
  count: number;
}

/** Matches by name, stages in order, each stage's tiles by shooter then camera. */
export function corpusRows(fixtures: ModelMapFixture[]): MatchRow[] {
  const byMatch = new Map<string, Map<number | null, ModelMapFixture[]>>();
  for (const f of fixtures) {
    const m = matchKey(f);
    if (!byMatch.has(m)) byMatch.set(m, new Map());
    const stages = byMatch.get(m)!;
    if (!stages.has(f.stage)) stages.set(f.stage, []);
    stages.get(f.stage)!.push(f);
  }
  return [...byMatch.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([match, stages]) => ({
      match,
      count: [...stages.values()].reduce((n, s) => n + s.length, 0),
      stages: [...stages.entries()]
        .sort(([a], [b]) => (a ?? 999) - (b ?? 999))
        .map(([stage, fx]) => ({
          stage,
          fixtures: [...fx].sort(
            (a, b) =>
              (a.shooter_id ?? "").localeCompare(b.shooter_id ?? "") ||
              a.camera_class.localeCompare(b.camera_class) ||
              a.stem.localeCompare(b.stem),
          ),
        })),
    }));
}

export function countBy<T extends string>(items: ModelMapFixture[], key: (f: ModelMapFixture) => T): Record<T, number> {
  const out = {} as Record<T, number>;
  for (const f of items) {
    const k = key(f);
    out[k] = (out[k] ?? 0) + 1;
  }
  return out;
}

// -- flow ----------------------------------------------------------------------

export interface FlowNode {
  id: string;
  label: string;
  column: number;
  color: string;
}

export interface FlowLink {
  source: string;
  target: string;
  value: number;
  /** Fixtures the band carries, for highlighting on the map. */
  stems: string[];
}

/**
 * Corpus -> camera class -> what the shipped build did with each fixture.
 * Every fixture travels exactly one path, so each column sums to the corpus.
 */
export function corpusFlow(fixtures: ModelMapFixture[]): { nodes: FlowNode[]; links: FlowLink[] } {
  const nodes: FlowNode[] = [
    { id: "corpus", label: "All fixtures", column: 0, color: "var(--color-ink-2)" },
    { id: "cls:headcam", label: "Headcam footage", column: 1, color: "var(--color-beep)" },
    { id: "cls:handheld", label: "Handheld footage", column: 1, color: "var(--color-manual)" },
    ...STATUS_ORDER.map((s) => ({
      id: `st:${s}`,
      label: STATUS_LABEL[s],
      column: 2,
      color: STATUS_COLOR[s],
    })),
  ];
  const links = new Map<string, FlowLink>();
  const add = (source: string, target: string, stem: string) => {
    const key = `${source}>${target}`;
    if (!links.has(key)) links.set(key, { source, target, value: 0, stems: [] });
    const l = links.get(key)!;
    l.value += 1;
    l.stems.push(stem);
  };
  for (const f of fixtures) {
    add("corpus", `cls:${f.camera_class}`, f.stem);
    add(`cls:${f.camera_class}`, `st:${fixtureStatus(f)}`, f.stem);
  }
  const used = new Set([...links.values()].flatMap((l) => [l.source, l.target]));
  return { nodes: nodes.filter((n) => used.has(n.id)), links: [...links.values()] };
}

export interface LaidNode extends FlowNode {
  x: number;
  y: number;
  h: number;
  value: number;
}

export interface LaidLink extends FlowLink {
  /** SVG path of the band (a filled ribbon). */
  d: string;
}

/**
 * A small Sankey layout: columns spread across ``width``, nodes stacked in
 * their given order with ``gap`` between them, heights proportional to the
 * larger of their in and out flow, bands leaving and entering in node order.
 */
export function layoutFlow(
  flow: { nodes: FlowNode[]; links: FlowLink[] },
  { width, height, nodeWidth = 12, gap = 14 }: { width: number; height: number; nodeWidth?: number; gap?: number },
): { nodes: LaidNode[]; links: LaidLink[] } {
  const inflow = new Map<string, number>();
  const outflow = new Map<string, number>();
  for (const l of flow.links) {
    outflow.set(l.source, (outflow.get(l.source) ?? 0) + l.value);
    inflow.set(l.target, (inflow.get(l.target) ?? 0) + l.value);
  }
  const value = (id: string) => Math.max(inflow.get(id) ?? 0, outflow.get(id) ?? 0);
  const columns = Math.max(1, ...flow.nodes.map((n) => n.column + 1));
  const perColumn = Array.from({ length: columns }, (_, c) => flow.nodes.filter((n) => n.column === c));
  // One scale for every column, set by the column that needs the most room.
  const scale = Math.min(
    ...perColumn.map((col) => {
      const total = col.reduce((s, n) => s + value(n.id), 0);
      const room = height - gap * Math.max(0, col.length - 1);
      return total > 0 ? room / total : Infinity;
    }),
  );
  const k = Number.isFinite(scale) ? scale : 0;
  const laid = new Map<string, LaidNode>();
  perColumn.forEach((col, c) => {
    const x = columns === 1 ? 0 : (c * (width - nodeWidth)) / (columns - 1);
    let y = 0;
    for (const n of col) {
      const v = value(n.id);
      const h = v * k;
      laid.set(n.id, { ...n, x, y, h, value: v });
      y += h + gap;
    }
  });
  const outOffset = new Map<string, number>();
  const inOffset = new Map<string, number>();
  const order = new Map(flow.nodes.map((n, i) => [n.id, i]));
  const sorted = [...flow.links].sort(
    (a, b) => (order.get(a.target) ?? 0) - (order.get(b.target) ?? 0) || (order.get(a.source) ?? 0) - (order.get(b.source) ?? 0),
  );
  const links: LaidLink[] = sorted.map((l) => {
    const s = laid.get(l.source)!;
    const t = laid.get(l.target)!;
    const h = l.value * k;
    const sy = s.y + (outOffset.get(l.source) ?? 0);
    const ty = t.y + (inOffset.get(l.target) ?? 0);
    outOffset.set(l.source, (outOffset.get(l.source) ?? 0) + h);
    inOffset.set(l.target, (inOffset.get(l.target) ?? 0) + h);
    const x0 = s.x + nodeWidth;
    const x1 = t.x;
    const mx = (x0 + x1) / 2;
    const d =
      `M${x0},${sy} C${mx},${sy} ${mx},${ty} ${x1},${ty} ` +
      `L${x1},${ty + h} C${mx},${ty + h} ${mx},${sy + h} ${x0},${sy + h} Z`;
    return { ...l, d };
  });
  return { nodes: [...laid.values()], links };
}

// -- holdout -----------------------------------------------------------------

export type SplitName = "stratified" | "grouped" | "lomo";

export const SPLIT_LABEL: Record<SplitName, string> = {
  stratified: "By candidate",
  grouped: "By stage",
  lomo: "Leave match out",
};

/** What a split holds together, in one line each. */
export const SPLIT_EXPLAIN: Record<SplitName, string> = {
  stratified:
    "5 folds over candidates. One stage's shots, and the same shots seen by a second camera, land on both sides, so this is close to in-sample. The shipped threshold is picked here.",
  grouped:
    "5 folds over stages. Every camera and every shooter on one stage stays in one fold, so no fold is scored on a bay it trained on.",
  lomo: "One fold per match: train on every other match, score the one left out. The closest thing to a new match.",
};

export interface FoldSummary {
  key: string;
  fixtures: number;
  headcam: number;
  handheld: number;
}

/**
 * The folds a split draws over the calibrating fixtures. Leave one match
 * out is one fold per match; grouped folds are drawn from stages, and since
 * the build does not record which stage landed in which of its five folds,
 * the groups themselves are what the page can show.
 */
export function foldsFor(split: SplitName, fixtures: ModelMapFixture[]): FoldSummary[] {
  const calibrating = fixtures.filter((f) => f.voter_c !== null);
  if (split === "stratified") return [];
  const key = (f: ModelMapFixture) =>
    split === "lomo" ? matchKey(f) : (f.stage_group ?? `${matchKey(f)}:${f.stage ?? "?"}`);
  const out = new Map<string, FoldSummary>();
  for (const f of calibrating) {
    const k = key(f);
    if (!out.has(k)) out.set(k, { key: k, fixtures: 0, headcam: 0, handheld: 0 });
    const s = out.get(k)!;
    s.fixtures += 1;
    s[f.voter_c!] += 1;
  }
  return [...out.values()].sort((a, b) => b.fixtures - a.fixtures || a.key.localeCompare(b.key));
}

/**
 * How much the honest splits cost against the by-candidate one, per head:
 * the recall and precision each loses at the shipped threshold.
 */
export function heldOutGap(head: VoterCHead): { split: SplitName; recall: number; precision: number }[] {
  const base = head.heldout.find((s) => s.split === "stratified")?.at_shipped_threshold;
  if (!base) return [];
  return head.heldout
    .filter((s) => s.split !== "stratified")
    .map((s) => ({
      split: s.split as SplitName,
      recall: s.at_shipped_threshold.recall - base.recall,
      precision: s.at_shipped_threshold.precision - base.precision,
    }));
}

// -- history -------------------------------------------------------------------

export interface VersionPoint {
  index: number;
  version: ModelVersion;
  total: number;
  headcam: number;
  handheld: number;
  changed: boolean;
}

/** One point per shipped version; ``changed`` marks those that moved the corpus. */
export function versionSeries(history: ModelVersion[]): VersionPoint[] {
  return history.map((v, index) => {
    const headcam = v.n_fixtures.headcam ?? 0;
    const handheld = v.n_fixtures.handheld ?? 0;
    const total = Object.values(v.n_fixtures).reduce((s, n) => s + n, 0);
    return { index, version: v, total, headcam, handheld, changed: v.added.length + v.removed.length > 0 };
  });
}

// -- held out, per fixture ---------------------------------------------------------

export type HeldOutTone = "clean" | "missed" | "extra";

/**
 * A fixture's leave-one-match-out outcome: every shot found and nothing
 * else (``clean``), a shot missed (``missed``, whatever else happened), or
 * only extra detections (``extra``). ``null`` when the build recorded none.
 */
export function heldOutTone(f: ModelMapFixture): HeldOutTone | null {
  const h = f.heldout_lomo;
  if (!h) return null;
  if (h.fn > 0) return "missed";
  if (h.fp > 0) return "extra";
  return "clean";
}

export const HELD_OUT_TONE_COLOR: Record<HeldOutTone, string> = {
  clean: "var(--color-done)",
  missed: "var(--color-live)",
  extra: "var(--color-manual)",
};

// -- comparing two models ------------------------------------------------------------

export type Membership = "both" | "a" | "b" | "neither";

export const MEMBERSHIP_COLOR: Record<Membership, string> = {
  both: "var(--color-ink-2)",
  a: "var(--color-beep)",
  b: "var(--color-manual)",
  neither: "var(--color-whisper)",
};

/** Which of the two models each fixture calibrated, by stem. */
export function corpusMembership(a: ModelMap, b: ModelMap): Record<string, Membership> {
  const inB = new Set(b.fixtures.filter((f) => f.voter_c).map((f) => f.stem));
  const out: Record<string, Membership> = {};
  for (const f of a.fixtures) {
    const ia = f.voter_c !== null;
    const ib = inB.has(f.stem);
    out[f.stem] = ia && ib ? "both" : ia ? "a" : ib ? "b" : "neither";
  }
  return out;
}

export interface HeadComparison {
  cameraClass: CameraClass;
  rows: { split: SplitName; a: SplitMetrics | null; b: SplitMetrics | null }[];
  matches: { match: string; a: SplitMetrics | null; b: SplitMetrics | null }[];
  /** Whether each model's build recorded per-match figures at all (older builds did not). */
  perMatch: { a: boolean; b: boolean };
}

/** Both models' figures per head, split by split and match by match. */
export function compareHeads(a: ModelMap, b: ModelMap): HeadComparison[] {
  const out: HeadComparison[] = [];
  for (const cls of ["headcam", "handheld"] as CameraClass[]) {
    const ha = a.ensemble.heads.find((h) => h.camera_class === cls) ?? null;
    const hb = b.ensemble.heads.find((h) => h.camera_class === cls) ?? null;
    if (!ha && !hb) continue;
    const at = (h: VoterCHead | null, s: SplitName) =>
      h?.heldout.find((x) => x.split === s)?.at_shipped_threshold ?? null;
    const ma = ha?.lomo_by_match ?? {};
    const mb = hb?.lomo_by_match ?? {};
    const matches = [...new Set([...Object.keys(ma), ...Object.keys(mb)])].sort();
    out.push({
      cameraClass: cls,
      rows: (["stratified", "grouped", "lomo"] as SplitName[]).map((s) => ({ split: s, a: at(ha, s), b: at(hb, s) })),
      matches: matches.map((m) => ({ match: m, a: ma[m] ?? null, b: mb[m] ?? null })),
      perMatch: { a: Object.keys(ma).length > 0, b: Object.keys(mb).length > 0 },
    });
  }
  return out;
}

export interface ParamRow {
  label: string;
  a: string;
  b: string;
  differs: boolean;
}

/** The parameters that describe each model, side by side. */
export function paramRows(a: ModelMap, b: ModelMap): ParamRow[] {
  const fmt = (v: number | null | undefined, d = 4) => (v === null || v === undefined ? "--" : v.toFixed(d));
  const rows: [string, string, string][] = [
    ["Target recall", fmt(a.ensemble.target_recall, 2), fmt(b.ensemble.target_recall, 2)],
    ["Match tolerance ms", fmt(a.ensemble.tolerance_ms, 0), fmt(b.ensemble.tolerance_ms, 0)],
    ["Voter C features", String(a.ensemble.feature_dim ?? "--"), String(b.ensemble.feature_dim ?? "--")],
  ];
  for (const cls of ["headcam", "handheld"] as CameraClass[]) {
    const ha = a.ensemble.heads.find((h) => h.camera_class === cls);
    const hb = b.ensemble.heads.find((h) => h.camera_class === cls);
    rows.push(
      [`${cls}: fixtures`, String(ha?.n_fixtures ?? "--"), String(hb?.n_fixtures ?? "--")],
      [`${cls}: candidates`, String(ha?.n_candidates ?? "--"), String(hb?.n_candidates ?? "--")],
      [`${cls}: shots`, String(ha?.n_positives ?? "--"), String(hb?.n_positives ?? "--")],
      [`${cls}: voter A floor`, fmt(ha?.voter_a_floor), fmt(hb?.voter_a_floor)],
      [`${cls}: voter B threshold`, fmt(ha?.voter_b_threshold), fmt(hb?.voter_b_threshold)],
      [`${cls}: voter C threshold`, fmt(ha?.voter_c_threshold), fmt(hb?.voter_c_threshold)],
    );
  }
  return rows.map(([label, va, vb]) => ({ label, a: va, b: vb, differs: va !== vb }));
}

// -- formatting ----------------------------------------------------------------

export function pct(x: number | null | undefined, digits = 1): string {
  return x === null || x === undefined ? "--" : `${(x * 100).toFixed(digits)}`;
}

export function bytes(n: number | null | undefined): string {
  if (!n) return "--";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} kB`;
  return `${n} B`;
}

/** ``stage-shots-<match>-stage<n>-<shooter>[-camera]`` -> the camera suffix, else "primary". */
export function angleOf(f: ModelMapFixture): string {
  const m = f.stem.match(/-stage\d+-s[0-9a-f]{8}(?:-(.+))?$/);
  return m?.[1] ?? "primary";
}
