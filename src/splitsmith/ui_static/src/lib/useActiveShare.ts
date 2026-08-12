/**
 * useActiveShare - resolves the match's live share URL for OWNER pages
 * (moment-followups follow-up to Share links MVP #541: share-aware
 * "Copy link at moment").
 *
 * ResultsStage and Compare's "Copy link at moment" buttons default to
 * copying an operator-only URL. When the match also has a live share
 * link, that copy should target the SHARE-scoped URL instead, so the
 * link works for whoever the owner actually shares it with. This hook
 * is the single place that answers "does this match have a live share,
 * and what's its URL" for that decision - same data ShareDialog already
 * fetches via api.listShares, read here instead of duplicated.
 *
 * Never fetches on a share view (recipients already hold their own
 * share-relative URL - isShareView) or when the page lacks the share
 * capability at all (local mode, or hosted before deployment mode has
 * resolved - the same capability source Results.tsx's canShare uses).
 * A failed fetch resolves to null silently: this is a copy-link
 * enhancement, never something that should block or break the existing
 * operator-URL copy path.
 */
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import { api } from "./api";
import { useDeploymentMode } from "./features";
import { isShareView } from "./shareView";

/** Strips exactly one trailing slash. ShareInfo.url is not guaranteed
 *  slash-free, and callers append their own `/results/...` path. */
function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

export interface ActiveShareState {
  /** URL of the match's first live (non-revoked) share, or null when
   *  there is none, the fetch failed, or fetching was gated off. */
  shareUrl: string | null;
}

export function useActiveShare(): ActiveShareState {
  const location = useLocation();
  const { mode } = useDeploymentMode();
  const canFetch = mode === "hosted" && !isShareView(location.pathname);

  const [shareUrl, setShareUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!canFetch) {
      setShareUrl(null);
      return;
    }
    let alive = true;
    api
      .listShares()
      .then((resp) => {
        if (!alive) return;
        const live = resp.shares.find((s) => s.revoked_at === null);
        setShareUrl(live ? stripTrailingSlash(live.url) : null);
      })
      .catch(() => {
        if (alive) setShareUrl(null);
      });
    return () => {
      alive = false;
    };
    // Fetch once per gate transition, not once per render - canFetch is
    // the only input that should re-trigger this.
  }, [canFetch]);

  return { shareUrl };
}
