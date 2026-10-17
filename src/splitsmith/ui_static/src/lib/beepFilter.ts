/**
 * The beep step's frequency filter (spec 2026-10-17-beep-spectral-view-design):
 * Off, the timer tone, or a custom band. With a band, the Audio row and Snap
 * read band-passed peaks, so the tone's rise shows under a cross-bay shot.
 */
import { useCallback, useState } from "react";

export type BeepFilter = { kind: "off" } | { kind: "tone" } | { kind: "custom"; lo: number; hi: number };

/** The detector's tonal band (``BeepDetectionConfig.tonal_band_*``); the
 *  server is authoritative, change both together. */
export const TIMER_BAND = { lo: 2200, hi: 3500 } as const;

const KEY = "splitsmith.beepFilter";

export function validBand(lo: number, hi: number): boolean {
  return Number.isFinite(lo) && Number.isFinite(hi) && lo >= 100 && hi <= 12000 && lo < hi;
}

/** The band a choice filters to. Timer tone is the timer's own band when the
 *  spectrum route found one (``tone_band``), else the detector's. */
export function bandOf(f: BeepFilter, toneBand?: readonly [number, number] | null): { lo: number; hi: number } | null {
  if (f.kind === "off") return null;
  if (f.kind === "tone") return toneBand ? { lo: toneBand[0], hi: toneBand[1] } : { ...TIMER_BAND };
  return { lo: f.lo, hi: f.hi };
}

export function bandParam(f: BeepFilter, toneBand?: readonly [number, number] | null): string | undefined {
  const b = bandOf(f, toneBand);
  return b ? `${Math.round(b.lo)}-${Math.round(b.hi)}` : undefined;
}

export function parseStored(raw: string | null): BeepFilter {
  try {
    const v = raw ? (JSON.parse(raw) as Partial<BeepFilter> & { lo?: number; hi?: number }) : null;
    if (v?.kind === "off") return { kind: "off" };
    if (v?.kind === "custom" && typeof v.lo === "number" && typeof v.hi === "number" && validBand(v.lo, v.hi)) {
      return { kind: "custom", lo: v.lo, hi: v.hi };
    }
  } catch {
    // A value an older build stored, or garbage: the default.
  }
  return { kind: "tone" };
}

export function useBeepFilter(): [BeepFilter, (f: BeepFilter) => void] {
  const [filter, setFilter] = useState<BeepFilter>(() => {
    try {
      return parseStored(window.localStorage.getItem(KEY));
    } catch {
      return { kind: "tone" };
    }
  });
  const set = useCallback((f: BeepFilter) => {
    setFilter(f);
    try {
      window.localStorage.setItem(KEY, JSON.stringify(f));
    } catch {
      // Private mode: the choice lasts the page.
    }
  }, []);
  return [filter, set];
}
