/**
 * Compare's audio mix (2026-10-02): every shooter on video is heard by
 * default, each at 1 / (number audible) -- the export's Mix rule
 * (``amix normalize=1``) applied to whoever is audible, so muting the
 * others never leaves a lone shooter quiet. A click mutes or unmutes one
 * shooter, Alt-click hears only that shooter (again: everyone back), and
 * one button mutes or unmutes all. Pure; the page applies it to its
 * video elements.
 */

/** The slugs heard, in roster order. */
export function audibleSlugs(slugs: string[], muted: ReadonlySet<string>): string[] {
  return slugs.filter((s) => !muted.has(s));
}

/** Each audible tile's volume: the sum of N uncorrelated microphones at
 *  1/N cannot clip. */
export function tileVolume(slugs: string[], muted: ReadonlySet<string>): number {
  const n = audibleSlugs(slugs, muted).length;
  return n > 0 ? 1 / n : 0;
}

export function toggleMute(muted: ReadonlySet<string>, slug: string): Set<string> {
  const next = new Set(muted);
  if (next.has(slug)) next.delete(slug);
  else next.add(slug);
  return next;
}

/** Hear only ``slug``; when that is already the case, hear everyone. */
export function solo(muted: ReadonlySet<string>, slug: string, slugs: string[]): Set<string> {
  const heard = audibleSlugs(slugs, muted);
  if (heard.length === 1 && heard[0] === slug) return new Set();
  return new Set(slugs.filter((s) => s !== slug));
}

export function allMuted(slugs: string[], muted: ReadonlySet<string>): boolean {
  return slugs.length > 0 && slugs.every((s) => muted.has(s));
}

/** The one button: unmute all when everyone is muted, else mute all. */
export function toggleAll(muted: ReadonlySet<string>, slugs: string[]): Set<string> {
  return allMuted(slugs, muted) ? new Set() : new Set(slugs);
}

/** What a moment link carries: ``cam`` when exactly one shooter is heard
 *  (the old single-audio links read the same), else the muted list. */
export function audioToMoment(slugs: string[], muted: ReadonlySet<string>): { cam?: string; mute?: string[] } {
  const heard = audibleSlugs(slugs, muted);
  if (heard.length === 1 && slugs.length > 1) return { cam: heard[0] };
  const mute = slugs.filter((s) => muted.has(s));
  return mute.length > 0 ? { mute } : {};
}

/** A moment link's audio: ``cam`` hears only that shooter, ``mute``
 *  silences the named ones, neither leaves everyone heard. */
export function audioFromMoment(slugs: string[], m: { cam?: string | null; mute?: string[] | null }): Set<string> {
  if (m.cam && slugs.includes(m.cam)) return new Set(slugs.filter((s) => s !== m.cam));
  return new Set((m.mute ?? []).filter((s) => slugs.includes(s)));
}
