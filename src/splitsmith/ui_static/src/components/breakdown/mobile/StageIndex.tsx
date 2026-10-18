/**
 * The phone Breakdown's start (``/breakdown/:slug``): pick the shooter and
 * the stage. Nothing opens by itself; the rows are ``lib/breakdownIndex``.
 */
import { ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ShooterChipStrip } from "@/components/match/ShooterChipStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { api, ApiError, type ShooterListEntry } from "@/lib/api";
import { indexRows, type IndexRow } from "@/lib/breakdownIndex";
import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";

export function StageIndex({ slug, shooters }: { slug: string; shooters: ShooterListEntry[] }) {
  const href = useMatchHref();
  const [rows, setRows] = useState<IndexRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .getProject(slug)
      .then((p) => {
        if (alive) setRows(indexRows(p.stages));
      })
      .catch((e) => {
        if (alive) setError(e instanceof ApiError ? e.detail : String(e));
      });
    return () => {
      alive = false;
    };
  }, [slug]);
  const name = shooters.find((s) => s.slug === slug)?.name;

  return (
    <div data-testid="mobile-breakdown-index" className="flex flex-col gap-3 px-4 py-3">
      <PageHeader className="mb-0" title="Breakdown" sub={shooters.length > 1 ? undefined : name}>
        <ShooterChipStrip shooters={shooters} activeSlug={slug} urlBase="breakdown" label={null} count={null} variant="inline" />
      </PageHeader>
      {error ? (
        <p role="alert" className="text-sm text-muted">
          {error}
        </p>
      ) : rows == null ? (
        <div className="flex h-32 items-center justify-center gap-2 text-md text-muted">
          <Loader2 className="size-4 animate-spin" /> Loading stages...
        </div>
      ) : rows.length === 0 ? (
        <p className="text-md text-muted">This shooter has no stages yet.</p>
      ) : (
        <ul className="flex flex-col">
          {rows.map((r) => {
            const body = (
              <>
                <span className="numeral w-7 shrink-0 text-md text-muted">{String(r.stage).padStart(2, "0")}</span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-base text-ink">{r.name}</span>
                  <span className="text-sm text-muted">{r.note}</span>
                </span>
                {r.open ? <ChevronRight className="size-4 shrink-0 text-muted" aria-hidden /> : null}
              </>
            );
            const cls = "flex min-h-14 items-center gap-3 border-b border-rule py-2";
            return (
              <li key={r.stage}>
                {r.open ? (
                  <Link to={href("breakdown", slug, String(r.stage))} className={cn(cls, "no-underline")}>
                    {body}
                  </Link>
                ) : (
                  <div className={cn(cls, "opacity-60")}>{body}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
