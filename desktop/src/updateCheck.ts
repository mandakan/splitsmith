/**
 * Update check, pure part. main.ts fetches the feed after the sidecar is
 * ready and the menu's "Check for updates..." asks on demand; both come
 * here to decide what to say. No Electron imports.
 */

export const UPDATE_FEED_URL = "https://splitsmith.app/desktop/latest.json";
export const FEED_TIMEOUT_MS = 5_000;

export interface UpdateFeed {
  version: string;
  url: string;
}

export type UpdateDecision =
  | { kind: "available"; version: string; url: string }
  | { kind: "dismissed"; version: string; url: string }
  | { kind: "current" }
  | { kind: "unknown" };

const VERSION_RE = /^v?\d+(\.\d+){0,2}$/;

/** Numeric per-component comparison; a missing component counts as zero. */
export function compareVersions(a: string, b: string): number {
  const parse = (v: string) => v.replace(/^v/, "").split(".").map((n) => Number(n) || 0);
  const pa = parse(a);
  const pb = parse(b);
  for (let i = 0; i < 3; i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

/** The feed body, or null for anything that is not exactly the shape we publish. */
export function parseFeed(body: unknown): UpdateFeed | null {
  if (typeof body !== "object" || body === null) return null;
  const { version, url } = body as Record<string, unknown>;
  if (typeof version !== "string" || !VERSION_RE.test(version)) return null;
  if (typeof url !== "string" || !url.startsWith("https://")) return null;
  return { version, url };
}

export function updateDecision(opts: {
  current: string;
  feed: UpdateFeed | null;
  dismissed: string | null;
}): UpdateDecision {
  if (!opts.feed) return { kind: "unknown" };
  if (compareVersions(opts.current, opts.feed.version) >= 0) return { kind: "current" };
  if (opts.dismissed !== null && compareVersions(opts.dismissed, opts.feed.version) === 0) {
    return { kind: "dismissed", ...opts.feed };
  }
  return { kind: "available", ...opts.feed };
}
