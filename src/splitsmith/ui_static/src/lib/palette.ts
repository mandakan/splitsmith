/**
 * Palette suggestions for the Look editor (issue #1273): a full palette from
 * one colour by a colour-theory scheme, accents ranked by how they stand out
 * from this match's footage, the warning when the current accent does not,
 * an accent series that keeps shooters apart, and four ready-made range
 * palettes. Pure; ``POST /api/shooters/{slug}/palette-sources`` measures the
 * footage and the logo, this decides.
 *
 * Distances are in OKLab, where equal steps look equally different, so "far
 * from the footage" means what a viewer sees, not what RGB says.
 */
import type { Rgb } from "@/lib/api";
import { contrastRatio, hexToRgb, rgbToHex } from "@/lib/lookEditor";

export interface Swatch {
  rgb: Rgb;
  share: number;
}

export type SchemeId = "complementary" | "analogous" | "triadic";

export const SCHEMES: { id: SchemeId; label: string; offset: number }[] = [
  { id: "complementary", label: "Complementary", offset: 180 },
  { id: "analogous", label: "Analogous", offset: 35 },
  { id: "triadic", label: "Triadic", offset: 120 },
];

export interface Suggestion {
  id: string;
  label: string;
  colors: Record<string, Rgb>;
  series: string[];
}

/** Whether ``draft`` already carries suggestion ``s``: every colour the
 *  suggestion sets and its accent series, so the card can say "In use". */
export function isApplied(
  s: Suggestion,
  draft: { colors: Record<string, Rgb>; accent_series: string[] },
): boolean {
  const same = (a: Rgb | undefined, b: Rgb) => !!a && a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
  return (
    Object.entries(s.colors).every(([token, rgb]) => same(draft.colors[token], rgb)) &&
    s.series.length === draft.accent_series.length &&
    s.series.every((hex, i) => hex.toLowerCase() === draft.accent_series[i]?.toLowerCase())
  );
}

// --- colour maths ------------------------------------------------------------------------

export function rgbToHsl([r, g, b]: Rgb): [number, number, number] {
  const [rn, gn, bn] = [r / 255, g / 255, b / 255];
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = max === rn ? (gn - bn) / d + (gn < bn ? 6 : 0) : max === gn ? (bn - rn) / d + 2 : (rn - gn) / d + 4;
  h *= 60;
  return [h, s, l];
}

export function hslToRgb([h, s, l]: [number, number, number]): Rgb {
  const hue = ((h % 360) + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = l - c / 2;
  const [r, g, b] =
    hue < 60 ? [c, x, 0] : hue < 120 ? [x, c, 0] : hue < 180 ? [0, c, x] : hue < 240 ? [0, x, c] : hue < 300 ? [x, 0, c] : [c, 0, x];
  return [r, g, b].map((v) => Math.round((v + m) * 255)) as Rgb;
}

export const hueOf = (c: Rgb): number => rgbToHsl(c)[0];

export function hueDistance(a: number, b: number): number {
  const d = Math.abs((((a - b) % 360) + 360) % 360);
  return Math.min(d, 360 - d);
}

export function hexToRgbSafe(hex: string): Rgb | null {
  return hexToRgb(hex);
}

function oklab([r, g, b]: Rgb): [number, number, number] {
  const lin = (c: number) => {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const [lr, lg, lb] = [lin(r), lin(g), lin(b)];
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

/** OKLCh hue in degrees and chroma: the hue a viewer names, and how much of it. */
function oklch(c: Rgb): { hue: number; chroma: number } {
  const [, a, b] = oklab(c);
  return { hue: ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360, chroma: Math.hypot(a, b) };
}

/** Below this chroma a swatch is grey: it has no hue to blend with. */
const GREY = 0.02;

/** Share-weighted hue separation of ``colour`` from the tinted swatches, 0..1. */
function hueSeparation(colour: Rgb, footage: Swatch[]): number {
  const tinted = footage.filter((f) => oklch(f.rgb).chroma >= GREY);
  const total = tinted.reduce((sum, f) => sum + f.share, 0);
  if (total === 0) return 1;
  const hue = oklch(colour).hue;
  return tinted.reduce((sum, f) => sum + (f.share / total) * (hueDistance(hue, oklch(f.rgb).hue) / 180), 0);
}

/** The smallest hue separation of ``colour`` from any tinted swatch, 0..1. */
function nearestHue(colour: Rgb, footage: Swatch[]): number {
  const tinted = footage.filter((f) => oklch(f.rgb).chroma >= GREY);
  if (tinted.length === 0) return 1;
  const hue = oklch(colour).hue;
  return Math.min(...tinted.map((f) => hueDistance(hue, oklch(f.rgb).hue) / 180));
}

export function oklabDistance(a: Rgb, b: Rgb): number {
  const [l1, a1, b1] = oklab(a);
  const [l2, a2, b2] = oklab(b);
  return Math.hypot(l1 - l2, a1 - a2, b1 - b2);
}

// --- one colour to a palette -----------------------------------------------------------------

const WHITE: Rgb = [255, 255, 255];

/** A hue at least ``gap`` degrees from ``avoid``, starting at ``want``. */
function awayFrom(want: number, avoid: number, gap = 35): number {
  if (hueDistance(want, avoid) >= gap) return want;
  const up = avoid + gap;
  const down = avoid - gap;
  return hueDistance(up, want) <= hueDistance(down, want) ? up : down;
}

/** Text that reads on ``fill``: white, else a near-white tint, else a dark shade of its hue. */
function textOn(fill: Rgb, hue: number): Rgb {
  if (contrastRatio(WHITE, fill) >= 4.5) return WHITE;
  for (let l = 0.16; l >= 0; l -= 0.04) {
    const dark = hslToRgb([hue, 0.5, l]);
    if (contrastRatio(dark, fill) >= 4.5) return dark;
  }
  return [0, 0, 0];
}

/** A full palette around ``accent`` by ``scheme``; the neutrals stay ``base``'s. */
export function paletteFrom(accent: Rgb, scheme: SchemeId, base: Record<string, Rgb>, footage: Swatch[] = []): Suggestion {
  const [h, s, l] = rgbToHsl(accent);
  const offset = SCHEMES.find((x) => x.id === scheme)?.offset ?? 180;
  let fill = hslToRgb([h, Math.max(s, 0.25), Math.min(l * 0.8, 0.45)]);
  let text = textOn(fill, h);
  if (contrastRatio(text, fill) < 4.5) {
    fill = hslToRgb([h, Math.max(s, 0.25), 0.3]);
    text = textOn(fill, h);
  }
  const colors: Record<string, Rgb> = {
    ...base,
    accent,
    accent_fill: fill,
    accent_text: text,
    split: hslToRgb([h + offset, 0.85, 0.6]),
    split_good: hslToRgb([awayFrom(140, h), 0.62, 0.55]),
    split_slow: hslToRgb([awayFrom(5, h), 0.8, 0.6]),
  };
  const label = SCHEMES.find((x) => x.id === scheme)?.label ?? scheme;
  return { id: `${scheme}-${rgbToHex(accent)}`, label, colors, series: accentSeries(accent, 6, footage) };
}

// --- the footage ---------------------------------------------------------------------------

/** How much ``colour`` stands out on footage made of ``footage`` swatches
 *  with ``average`` behind the text, 0..1. Mostly hue: range footage is dull
 *  greens, browns and sand, and a vivid green still reads as the grass. So
 *  its share-weighted hue separation from the tinted swatches (greys have no
 *  hue to blend with), the nearest one counted twice, and its contrast with
 *  the average. */
export function standOut(colour: Rgb, footage: Swatch[], average: Rgb | null): number {
  if (footage.length === 0) return 1;
  const fromHue = (hueSeparation(colour, footage) + 2 * Math.min(nearestHue(colour, footage) * 3, 1)) / 3;
  const fromAverage = average ? Math.min(contrastRatio(colour, average) / 3, 1) : 1;
  return 0.75 * fromHue + 0.25 * fromAverage;
}

/** ``n`` accents that stand out on the footage, each at least 45 degrees of
 *  hue from the others, best first. */
export function footageAccents(footage: Swatch[], average: Rgb | null, n = 4): Rgb[] {
  const candidates: { c: Rgb; score: number }[] = [];
  for (let hue = 0; hue < 360; hue += 15) {
    const c = hslToRgb([hue, 0.85, 0.55]);
    candidates.push({ c, score: standOut(c, footage, average) });
  }
  candidates.sort((a, b) => b.score - a.score);
  const picks: Rgb[] = [];
  for (const { c } of candidates) {
    if (picks.every((p) => hueDistance(hueOf(p), hueOf(c)) >= 45)) picks.push(c);
    if (picks.length === n) break;
  }
  return picks;
}

/** A warning when the palette's accent blends into the footage and a
 *  clearly better one exists; null otherwise (never blocks anything). */
export function weakAccent(
  colors: Record<string, Rgb>,
  footage: Swatch[],
  average: Rgb | null,
): { message: string; better: Rgb } | null {
  const accent = colors.accent;
  if (!accent || footage.length === 0) return null;
  const mine = standOut(accent, footage, average);
  const [better] = footageAccents(footage, average, 1);
  if (!better || mine >= 0.5 || standOut(better, footage, average) - mine < 0.15) return null;
  return {
    message: `Your accent blends into this footage's colours; ${rgbToHex(better)} stands out more.`,
    better,
  };
}

/** ``n`` colours for shooters without their own accent: the seed first, then
 *  each next the candidate farthest from those chosen and from the footage. */
export function accentSeries(seed: Rgb, n = 6, footage: Swatch[] = []): string[] {
  const candidates: Rgb[] = [];
  for (let hue = 0; hue < 360; hue += 10) candidates.push(hslToRgb([hue, 0.8, 0.58]));
  const chosen: Rgb[] = [seed];
  while (chosen.length < n) {
    let best: Rgb | null = null;
    let bestScore = -1;
    for (const c of candidates) {
      // Shooters apart first: the smallest hue gap to those chosen. Then a
      // shooter's colour should not be the grass either: a near hue costs.
      // Both measures: a hue gap alone lets yellow sit next to green, an
      // OKLab gap alone lets four magentas of different lightness through.
      const apart = Math.min(...chosen.map((x) => hueDistance(hueOf(c), hueOf(x)) / 360 + oklabDistance(c, x)));
      const fromFootage = footage.length ? Math.min(nearestHue(c, footage) * 3, 1) : 1;
      const score = apart + 0.15 * fromFootage;
      if (score > bestScore) {
        bestScore = score;
        best = c;
      }
    }
    if (!best) break;
    chosen.push(best);
  }
  return chosen.map(rgbToHex);
}

// --- ready-made ------------------------------------------------------------------------------

export const READY_MADE: { id: string; label: string; help: string; accent: Rgb; scheme: SchemeId }[] = [
  { id: "outdoor", label: "Outdoor range", help: "Magenta red against grass and berms", accent: [228, 32, 96], scheme: "complementary" },
  { id: "indoor", label: "Indoor range", help: "Bright cyan for dim halls", accent: [0, 190, 232], scheme: "analogous" },
  { id: "sand", label: "Sand and desert", help: "Deep blue against sand and sky glare", accent: [36, 110, 240], scheme: "complementary" },
  { id: "snow", label: "Snow and winter", help: "Warm orange against white and grey", accent: [246, 104, 20], scheme: "triadic" },
];
