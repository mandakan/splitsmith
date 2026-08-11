/**
 * StageCompareLink -- per-stage "compare shooters" CTA.
 *
 * Shared by the results/share overview (Results.tsx) and the match summary
 * (Home.tsx) so the two surfaces stay in sync by construction: same icon, same
 * gating, same destination. It resolves its href through useMatchHref, so it
 * lands on /share/{token}/compare/{stage} in the anonymous share context and
 * /match/{matchId}/compare/{stage} for the owner -- no per-context wiring.
 *
 * Desktop-only (hidden below lg): the Compare route sits behind DesktopGate, so
 * we don't offer an entry point where it can't open. Renders nothing unless at
 * least two shooters have a watchable (audited) run on the stage -- compare
 * needs two tiles, and this avoids a CTA that opens compare's empty state.
 */
import { GitCompare } from "lucide-react";
import { Link } from "react-router-dom";

import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";

interface StageCompareLinkProps {
  stageNumber: number;
  /** Shooters with a watchable (audited) run on this stage -- i.e. the row's
   *  ``auditedCount`` from ``buildStageMatrix``. The CTA hides below 2. */
  comparableCount: number;
  className?: string;
}

export function StageCompareLink({
  stageNumber,
  comparableCount,
  className,
}: StageCompareLinkProps) {
  const href = useMatchHref();
  if (comparableCount < 2) return null;
  const label = `Compare shooters on stage ${stageNumber}`;
  return (
    <Link
      to={href("compare", String(stageNumber))}
      title={label}
      aria-label={label}
      className={cn(
        "hidden size-7 shrink-0 items-center justify-center rounded-full border border-rule text-muted transition-colors hover:border-led hover:text-led focus-visible:border-led focus-visible:text-led focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led lg:inline-flex",
        className,
      )}
    >
      <GitCompare className="size-3.5" aria-hidden />
    </Link>
  );
}
