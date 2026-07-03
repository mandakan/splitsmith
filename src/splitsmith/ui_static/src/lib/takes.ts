/**
 * Multi-stage take helpers (one long recording covering N stages).
 *
 * A "take" is a RawVideo manifest entry whose ``covers_stages`` lists two
 * or more stages. Its canonical identity key is the StageVideo path string
 * ``raw/<filename>`` - the same in local and hosted mode (the take
 * endpoints reconstruct it from a filename query/route param). Legacy
 * backfilled entries can carry absolute disk paths; those are not
 * addressable by the take endpoints, so the helpers treat them as
 * non-takes rather than emitting links that 404.
 */

import type { RawVideoManifestEntry } from "@/lib/api";

/** Filename usable in take routes and endpoints, or null when the entry's
 *  ``storage_path`` is not the canonical ``raw/<filename>`` shape. */
export function takeFilename(rv: RawVideoManifestEntry): string | null {
  if (!rv.storage_path.startsWith("raw/")) return null;
  const name = rv.storage_path.slice("raw/".length);
  if (!name || name.includes("/")) return null;
  return name;
}

/** The addressable multi-stage take a video path belongs to, or null when
 *  the path is not a take (single-stage clip, unknown path, or a legacy
 *  entry the take endpoints cannot resolve). */
export function findTakeForPath(
  rawVideos: RawVideoManifestEntry[] | null | undefined,
  videoPath: string,
): RawVideoManifestEntry | null {
  for (const rv of rawVideos ?? []) {
    if (rv.storage_path !== videoPath) continue;
    if (rv.covers_stages.length < 2) return null;
    return takeFilename(rv) != null ? rv : null;
  }
  return null;
}
