/**
 * matchHref -- build URLs that stay inside the active match's subtree.
 *
 * Match-scoped SPA routes live under ``/match/:matchId/...`` (#353
 * Phase 3). Pages that build links / fire ``navigate()`` calls need to
 * keep that prefix on so a click doesn't fall out of the match scope.
 *
 * ``useMatchHref()`` returns a builder bound to the current URL's
 * matchId (read via ``useParams``). Call it with the suffix you want
 * to land on:
 *
 *     const href = useMatchHref();
 *     navigate(href("audit", slug, String(stage)));
 *     // -> "/match/<id>/audit/<slug>/<stage>"
 *
 * Missing matchId (e.g. legacy URL before the redirect kicks in) falls
 * back to a leading slash so the result still parses as a valid path.
 */

import { useCallback } from "react";
import { useLocation, useParams } from "react-router-dom";

export type MatchHrefBuilder = (...segments: string[]) => string;

export function useMatchHref(): MatchHrefBuilder {
  const { matchId, token } = useParams<{ matchId?: string; token?: string }>();
  const { pathname } = useLocation();
  const shareToken = pathname.startsWith("/share/") ? token : undefined;
  return useCallback(
    (...segments: string[]) => {
      const tail = segments
        .filter((s) => s != null && s !== "")
        .map((s) => encodeURIComponent(s))
        .join("/");
      if (shareToken) {
        return `/share/${encodeURIComponent(shareToken)}/${tail}`;
      }
      if (matchId) {
        return `/match/${encodeURIComponent(matchId)}/${tail}`;
      }
      return `/${tail}`;
    },
    [matchId, shareToken],
  );
}

/** Href for the take-overview page of one multi-stage raw recording.
 *  ``filename`` is the raw video's basename (the take endpoints key on
 *  ``raw/<filename>``); ``matchId`` comes from ``useParams`` at the
 *  callsite. */
export function takeHref(
  matchId: string | null | undefined,
  slug: string,
  filename: string,
): string {
  return matchHref(matchId, "take", slug, filename);
}

/** Standalone helper for places that have ``matchId`` in hand (e.g. the
 *  match shell, the picker after bind) but aren't ready to wire a hook. */
export function matchHref(
  matchId: string | null | undefined,
  ...segments: string[]
): string {
  const tail = segments
    .filter((s) => s != null && s !== "")
    .map((s) => encodeURIComponent(s))
    .join("/");
  if (matchId) {
    return `/match/${encodeURIComponent(matchId)}/${tail}`;
  }
  return `/${tail}`;
}
