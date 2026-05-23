/**
 * ShooterScopedRoute -- shooter-bound pages live under /<page>/:slug[/...].
 *
 * The slug in the URL is the only source of truth for which shooter the
 * page reads (#353). When the slug changes, the wrapped element remounts
 * via the slug-derived key so each page's effects start fresh on the new
 * shooter without page-specific reset plumbing.
 *
 * Slugless visits (e.g. ``/audit``) redirect to ``/shooters`` so the user
 * picks one explicitly -- there is no "active shooter" fallback any more.
 */

import { Navigate, useParams } from "react-router-dom";

interface Props {
  element: React.ReactElement;
}

export function ShooterScopedRoute({ element }: Props) {
  const { slug, matchId } = useParams<{ slug: string; matchId?: string }>();
  if (!slug) {
    return (
      <Navigate
        to={matchId ? `/match/${matchId}/shooters` : "/shooters"}
        replace
      />
    );
  }
  return (
    <span key={slug} style={{ display: "contents" }}>
      {element}
    </span>
  );
}
