/**
 * The highlight reel on the Overview (spec 2026-10-18-highlight-reel-design,
 * part 2): how close the match is to unlocking it, the stages in the way
 * with a link to each fix, then the way into Export's reel mode. One row in
 * SyncCard's shape; its button is `default` because the page's primary is
 * the next-step action.
 */
import { Clapperboard } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { type HighlightReadiness, api } from "@/lib/api";
import { blockerFix, blockingStages, readinessLine, showReelCard } from "@/lib/highlightReel";

/** Blockers listed before the rest fold into "and N more". */
const SHOWN_BLOCKERS = 3;

export function HighlightReelCard({
  slug,
  href,
  version,
}: {
  slug: string;
  href: (...segments: string[]) => string;
  /** Anything that changes when a stage's state may have: re-reads it. */
  version: unknown;
}) {
  const [readiness, setReadiness] = useState<HighlightReadiness | null>(null);

  useEffect(() => {
    let live = true;
    api
      .getHighlights(slug)
      .then((r) => {
        if (live) setReadiness(r);
      })
      .catch(() => {
        if (live) setReadiness(null);
      });
    return () => {
      live = false;
    };
  }, [slug, version]);

  if (readiness == null || !showReelCard(readiness)) return null;
  const blockers = blockingStages(readiness);
  const shown = blockers.slice(0, SHOWN_BLOCKERS);
  return (
    <section aria-label="Highlight reel" className="mb-4 rounded-[10px] border border-rule bg-surface px-3.5 py-2">
      <div className="flex flex-wrap items-center gap-3">
        <span
          aria-hidden="true"
          className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-md border border-rule-strong text-muted"
        >
          <Clapperboard className="size-3" />
        </span>
        <h2 className="text-md font-medium text-ink">Highlight reel</h2>
        <p className="text-sm text-muted">{readinessLine(readiness)}</p>
        <div className="ml-auto flex items-center gap-2">
          <Button asChild>
            <Link to={`${href("export", slug)}?mode=reel`}>
              {readiness.unlocked ? "Make highlight reel" : "Choose moments"}
            </Link>
          </Button>
        </div>
      </div>
      {shown.length > 0 ? (
        <ul className="mt-1.5 space-y-0.5 pl-[34px] text-sm">
          {shown.map((s) => {
            const fix = blockerFix(s, slug, href);
            return (
              <li key={s.stage_number}>
                <Link to={fix.to} className="text-muted underline-offset-2 hover:text-ink hover:underline">
                  {fix.text}
                </Link>
              </li>
            );
          })}
          {blockers.length > shown.length ? (
            <li className="text-muted">and {blockers.length - shown.length} more</li>
          ) : null}
        </ul>
      ) : null}
    </section>
  );
}
