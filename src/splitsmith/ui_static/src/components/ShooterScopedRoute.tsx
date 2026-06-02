/**
 * ShooterScopedRoute -- shooter-bound pages live under /<page>/:slug[/...].
 *
 * The slug in the URL is the only source of truth for which shooter the
 * page reads (#353). When the slug changes, the wrapped element remounts
 * via the slug-derived key so each page's effects start fresh on the new
 * shooter without page-specific reset plumbing.
 *
 * A slug-less visit shouldn't happen -- every route that mounts this
 * supplies ``:slug`` -- but if one ever slips through, resolve it to the
 * default shooter via ``DefaultShooterRedirect`` (base derived from the
 * first path segment) rather than dumping the operator on the shooter
 * list. The list is only the right destination for a genuinely empty
 * match, which ``DefaultShooterRedirect`` already handles.
 */

import { useLocation, useParams } from "react-router-dom";

import { DefaultShooterRedirect } from "@/components/match/DefaultShooterRedirect";

interface Props {
  element: React.ReactElement;
}

type ScopedBase = "audit" | "coach" | "export" | "ingest";

function baseFromPath(pathname: string): ScopedBase {
  // Path is /match/:matchId/<base>/... -- the segment after matchId names
  // the page. Default to "audit" if the shape is unexpected.
  const segs = pathname.split("/").filter(Boolean);
  const idx = segs.indexOf("match");
  const candidate = idx >= 0 ? segs[idx + 2] : segs[0];
  if (
    candidate === "coach" ||
    candidate === "export" ||
    candidate === "ingest"
  ) {
    return candidate;
  }
  return "audit";
}

export function ShooterScopedRoute({ element }: Props) {
  const { slug } = useParams<{ slug: string; matchId?: string }>();
  const { pathname } = useLocation();
  if (!slug) {
    return <DefaultShooterRedirect base={baseFromPath(pathname)} />;
  }
  return (
    <span key={slug} style={{ display: "contents" }}>
      {element}
    </span>
  );
}
