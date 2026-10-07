/**
 * The Look editor's data (spec 2026-10-07 s5, #1264): which colour tokens
 * it shows and what each one is for, the draft's errors (each keyed by
 * the field it names, and what disables Save), contrast warnings (shown,
 * never blocking: the renderer draws any colour), the card styles a Look
 * offers, the name a duplicate gets, and the preview requests. The sheet
 * maps these to primitives and owns no rule.
 */
import { ApiError, type ExportPreviewBody, type LookInfo, type PreviewCard, type Rgb, type StoredLookBody, type TemplateEdit } from "@/lib/api";

export type LookDraft = StoredLookBody;

export interface TokenRow {
  token: string;
  /** Where the colour shows, in a phrase. */
  help: string;
  /** Not every Look has it; the renderer has a fallback. */
  optional?: boolean;
}

export const TOKEN_GROUPS: { group: string; tokens: TokenRow[] }[] = [
  {
    group: "Text",
    tokens: [
      { token: "ink", help: "Names, figures, the main card text" },
      { token: "ink_2", help: "Secondary lines: the date, the division" },
      { token: "muted", help: "Labels such as SCORING and SPLITS" },
      { token: "subtle", help: "Faint helper text" },
    ],
  },
  {
    group: "Surfaces",
    tokens: [
      { token: "surface", help: "The card backdrop when there is no frame" },
      { token: "rule", help: "Hairlines between bands" },
      { token: "stroke", help: "The outline behind text on footage" },
    ],
  },
  {
    group: "Accent",
    tokens: [
      { token: "accent", help: "The brand colour: rules, the sting band" },
      { token: "accent_fill", help: "Filled badges (DQ)" },
      { token: "accent_text", help: "Text on the accent fill" },
    ],
  },
  {
    group: "Splits",
    tokens: [
      { token: "split", help: "The current split" },
      { token: "split_good", help: "A fast split, Alphas" },
      { token: "split_slow", help: "A slow split, misses", optional: true },
    ],
  },
];

const REQUIRED = TOKEN_GROUPS.flatMap((g) =>
  g.tokens.filter((t) => !t.optional).map((t) => t.token),
);
const HEX = /^#[0-9a-fA-F]{6}$/;
const NAME = /^[a-z][a-z0-9_-]{0,31}$/;
export const MAX_LABEL = 60;

export function rgbToHex([r, g, b]: Rgb): string {
  return `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

export function hexToRgb(hex: string): Rgb | null {
  if (!HEX.test(hex)) return null;
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** What blocks Save, keyed by field (``label``, ``colors.<token>``,
 *  ``accent_series.<i>``), each worded with the fix. Mirrors the server's
 *  validation so Save is never refused after the fact. */
export function draftErrors(draft: LookDraft): Record<string, string> {
  const errors: Record<string, string> = {};
  if (draft.label.length > MAX_LABEL)
    errors.label = `Keep the name under ${MAX_LABEL} characters.`;
  for (const token of REQUIRED) {
    if (!draft.colors[token])
      errors[`colors.${token}`] = `Pick a colour for ${token}.`;
  }
  draft.accent_series.forEach((colour, i) => {
    if (!HEX.test(colour))
      errors[`accent_series.${i}`] =
        `Use a #rrggbb colour, not ${JSON.stringify(colour)}.`;
  });
  return errors;
}

function luminance([r, g, b]: Rgb): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** WCAG 2 contrast ratio, 1 to 21. */
export function contrastRatio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const PAIRS: { token: string; on: string; what: string }[] = [
  { token: "accent_text", on: "accent_fill", what: "Text on the accent fill" },
  { token: "ink", on: "surface", what: "Card text on the backdrop" },
];
const MIN_CONTRAST = 4.5;

/** Pairs that will be hard to read. Shown beside the colour; Save stays on. */
export function contrastWarnings(
  colors: Record<string, Rgb>,
): { token: string; message: string }[] {
  return PAIRS.flatMap(({ token, on, what }) => {
    const fg = colors[token];
    const bg = colors[on];
    if (!fg || !bg) return [];
    const ratio = contrastRatio(fg, bg);
    return ratio >= MIN_CONTRAST
      ? []
      : [
          {
            token,
            message: `${what} is hard to read (contrast ${ratio.toFixed(1)}:1; aim for ${MIN_CONTRAST}:1).`,
          },
        ];
  });
}

export const CARD_STYLE_SLOTS: { slot: string; label: string }[] = [
  { slot: "title_page", label: "Title page" },
  { slot: "slate", label: "Stage slate" },
  { slot: "lower_third", label: "Lower third" },
  { slot: "closing", label: "Closing card" },
];

/** The variants a card slot of ``look`` offers, ``default`` first. */
export function styleOptions(
  look: LookInfo | undefined,
  slot: string,
): string[] {
  const names = (look?.slots[slot] ?? []).map((v) => v.name);
  return names.includes("default") ? names : ["default", ...names];
}

/** ``draft`` with ``slot`` drawing ``variant``; ``default`` is stored as no entry. */
export function setStyle(
  draft: LookDraft,
  slot: string,
  variant: string,
): LookDraft {
  const styles = { ...draft.styles };
  if (variant === "default") delete styles[slot];
  else styles[slot] = variant;
  return { ...draft, styles };
}

/** ``<source>-copy`` (then ``-copy-2`` ...), shortened to fit a Look name. */
export function duplicateName(
  source: string,
  taken: readonly string[],
): string {
  for (let i = 1; ; i += 1) {
    const suffix = i === 1 ? "-copy" : `-copy-${i}`;
    const name = `${source.slice(0, 32 - suffix.length)}${suffix}`;
    if (NAME.test(name) && !taken.includes(name)) return name;
  }
}

export function isDirty(saved: LookDraft, draft: LookDraft): boolean {
  const norm = (d: LookDraft) =>
    JSON.stringify({
      label: d.label,
      base: d.base,
      colors: Object.fromEntries(
        Object.entries(d.colors).sort(([a], [b]) => a.localeCompare(b)),
      ),
      accent_series: d.accent_series,
      styles: Object.fromEntries(
        Object.entries(d.styles).sort(([a], [b]) => a.localeCompare(b)),
      ),
    });
  return norm(saved) !== norm(draft);
}

export interface PreviewCardInfo {
  card: PreviewCard;
  label: string;
  /** How long the card runs: the slider's range. */
  seconds: number;
  /** Drawn by a template, so the slider means something. */
  animated: boolean;
}

export const PREVIEW_CARDS: PreviewCardInfo[] = [
  { card: "title", label: "Title page", seconds: 3, animated: true },
  { card: "slate", label: "Slate", seconds: 1.5, animated: true },
  { card: "lower-third", label: "Lower third", seconds: 1.5, animated: true },
  { card: "summary", label: "Summary", seconds: 3, animated: false },
  { card: "closing", label: "Closing", seconds: 3, animated: true },
  { card: "sting", label: "Sting", seconds: 1, animated: true },
];

/** The export-preview body for one card of the draft. ``at`` null is the
 *  card's poster; ``sting`` names the sting the ``sting`` card draws. */
export function previewRequest(args: {
  card: PreviewCard;
  look: string;
  draft: LookDraft;
  stageNumber: number;
  width: number;
  at: number | null;
  sting: string | null;
  /** A card's template variant other than the Look's default (the template editor). */
  variant?: string;
  /** The template editor's unsaved text. */
  templates?: TemplateEdit[];
}): ExportPreviewBody {
  const body: ExportPreviewBody = {
    card: args.card,
    stage_number: args.stageNumber,
    width: args.width,
    look: args.look,
    draft: args.draft,
  };
  if (args.card === "sting" && args.sting) body.variant = args.sting;
  else if (args.variant && args.variant !== "default") body.variant = args.variant;
  if (args.at !== null) body.at = args.at;
  if (args.templates && args.templates.length > 0) body.templates = args.templates;
  return body;
}

/** Runs preview renders one at a time, in the order asked: the server
 *  draws one card per process at a time and answers 429 to the rest, so
 *  the editor's strip of six would mostly come back busy. A 429 is tried
 *  again after a pause; any other error is the caller's. */
export function serialQueue(opts: { retries?: number; retryDelayMs?: number } = {}) {
  const retries = opts.retries ?? 4;
  const delay = opts.retryDelayMs ?? 750;
  let tail: Promise<unknown> = Promise.resolve();
  return function enqueue<T>(task: () => Promise<T>): Promise<T> {
    const run = async (): Promise<T> => {
      for (let attempt = 0; ; attempt += 1) {
        try {
          return await task();
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 429) || attempt >= retries) throw e;
          await new Promise((r) => setTimeout(r, delay));
        }
      }
    };
    const result = tail.then(run, run);
    tail = result.catch(() => undefined);
    return result;
  };
}
