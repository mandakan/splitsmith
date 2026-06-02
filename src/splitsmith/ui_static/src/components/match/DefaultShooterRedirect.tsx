/**
 * DefaultShooterRedirect -- resolve a slug-less per-shooter URL to a
 * shooter instead of dumping the operator on the shooter list (#477
 * follow-up).
 *
 * Per-shooter pages (Audit / Coach / Export / Ingest) carry the shooter as
 * a URL slug. A bare visit -- a bookmark, a link that dropped the slug, or
 * a single-shooter match -- used to ``Navigate`` straight to ``/shooters``,
 * which is pointless friction when there's an obvious shooter to open.
 * This resolves the default shooter (see ``pickDefaultShooterSlug``) and
 * redirects there; it only falls back to the list when the match genuinely
 * has zero shooters, where the empty-state list is the right destination.
 *
 * Self-fetches the roster rather than reading outlet context: the
 * audit/coach/export bare routes mount under ``MatchShell`` but the ingest
 * bare route lives outside it, so a single self-contained fetch is the one
 * path that works everywhere. This only runs on the bare-route edge case,
 * not the common slug-carrying path, so the extra GET is cheap.
 */

import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";

import { api } from "@/lib/api";
import { pickDefaultShooterSlug } from "@/lib/defaultShooter";
import { matchHref } from "@/lib/matchHref";

interface Props {
  base: "audit" | "coach" | "export" | "ingest";
}

export function DefaultShooterRedirect({ base }: Props) {
  const { matchId } = useParams<{ matchId?: string }>();
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .listMatchShooters()
      .then((r) => {
        if (!alive) return;
        const slug = pickDefaultShooterSlug(r.shooters);
        setTarget(
          slug
            ? matchHref(matchId, base, slug)
            : matchHref(matchId, "shooters"),
        );
      })
      .catch(() => {
        // Unknown match / transient failure: the list handles both the
        // empty state and the "switch project" escape hatch.
        if (alive) setTarget(matchHref(matchId, "shooters"));
      });
    return () => {
      alive = false;
    };
  }, [matchId, base]);

  // Brief: the roster fetch resolves fast and this only renders on the
  // (rare) slug-less route. Render nothing rather than flash the list.
  if (!target) return null;
  return <Navigate to={target} replace />;
}
