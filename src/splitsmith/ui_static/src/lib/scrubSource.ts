/**
 * Which file the Audit players stream where they would pin the trim.
 *
 * The audit trim is a full-resolution scrub cache: from a 4K headcam it
 * runs ~150 Mbit/s, which stalls software decode, a NAS, and Chromium's
 * low-end device mode (playback ends after ~2 s). Its 720p rendition is
 * cut from the trim and shares its timeline frame for frame, so offsets
 * do not change. The rendition is used only when the server named a fresh
 * one (``scrub_version``), the user has not asked for full resolution,
 * and it has not already failed to play on this page.
 */
export interface ScrubChoice {
  kind: "web" | "trim";
  version: string | null;
}

export function scrubSource(args: {
  trimVersion: string | null | undefined;
  scrubVersion: string | null | undefined;
  fullRes: boolean;
  failed: boolean;
}): ScrubChoice {
  const { trimVersion, scrubVersion, fullRes, failed } = args;
  if (scrubVersion && !fullRes && !failed) return { kind: "web", version: scrubVersion };
  return { kind: "trim", version: trimVersion ?? null };
}
