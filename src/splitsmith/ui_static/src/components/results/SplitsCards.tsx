/**
 * SplitsCards -- the Splits table at phone width (UX PR 4). One card per
 * stage: ordinal, name and the play link on the first line; the splits
 * and time on the second; HF and hits dimmed on the third. A collapsed
 * run or a not-audited stage is one dim line. Multi-shooter matches show
 * the lead (or filtered) shooter's cell; the desktop table is the
 * per-shooter surface.
 */
import { Link } from "react-router-dom";

import { formatClock } from "@/lib/overview";
import { formatHits, type SplitsRow } from "@/lib/splitsTable";

import { PlayLink, type SplitsHrefs } from "./SplitsTable";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export interface SplitsCardsProps {
  rows: SplitsRow[];
  share: boolean;
  hrefs: SplitsHrefs;
}

function Figure({ label, value, after }: { label?: string; value: string; after?: string }) {
  return (
    <span className="whitespace-nowrap">
      {label ? `${label} ` : ""}
      <b className="font-medium text-ink">{value}</b>
      {after ? ` ${after}` : ""}
    </span>
  );
}

export function SplitsCards({ rows, share, hrefs }: SplitsCardsProps) {
  const card = "rounded-[10px] border border-rule bg-surface px-3.5 py-3 text-md";
  return (
    <div className="flex flex-col gap-2">
      {rows.map((row) => {
        if (row.kind === "collapsed") {
          return (
            <div key={`c${row.from}`} className={`${card} flex items-center justify-between gap-3 text-subtle`}>
              <span className="min-w-0 truncate">
                <span className="mr-2 font-mono text-sm">
                  {row.count === 1 ? pad2(row.from) : `${pad2(row.from)}–${pad2(row.to)}`}
                </span>
                {row.count === 1 ? row.firstName : `${row.firstName} to ${row.lastName}`}
              </span>
              <span className="shrink-0 font-mono text-sm">{row.reason === "no_video" ? "no video" : "no footage"}</span>
            </div>
          );
        }
        const { stageNumber: n, lead } = row;
        if (!lead?.audited) {
          return (
            <div key={n} className={`${card} flex items-center justify-between gap-3 text-subtle`}>
              <span className="min-w-0 truncate">
                <span className="mr-2 font-mono text-sm">{pad2(n)}</span>
                {row.stageName}
              </span>
              {share ? (
                <span className="shrink-0 font-mono text-sm">no video</span>
              ) : lead ? (
                <Link to={hrefs.audit(lead.slug, n)} className="shrink-0 font-mono text-sm text-ink-2">
                  not audited &middot; Audit
                </Link>
              ) : (
                <span className="shrink-0 font-mono text-sm">not audited</span>
              )}
            </div>
          );
        }
        return (
          <section key={n} aria-label={`Stage ${n} ${row.stageName}`} className={card}>
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm text-muted">{pad2(n)}</span>
              <span className="min-w-0 flex-1 truncate font-medium text-ink">{row.stageName}</span>
              <PlayLink to={hrefs.stage(lead.slug, n)} label={`Play stage ${n}`} />
            </div>
            <div className="numeral mt-1.5 flex flex-wrap gap-x-2.5 gap-y-0.5 text-sm text-muted">
              <Figure label="draw" value={lead.draw == null ? "—" : lead.draw.toFixed(2)} />
              <Figure label="avg" value={lead.avgSplit == null ? "—" : lead.avgSplit.toFixed(3)} />
              <Figure label="fast" value={lead.fastestSplit == null ? "—" : lead.fastestSplit.toFixed(3)} />
              <Figure value={String(lead.shotCount)} after="shots" />
              {lead.timeSeconds > 0 ? <Figure value={formatClock(lead.timeSeconds)} /> : null}
            </div>
            {lead.scorecard ? (
              <div className="mt-1 font-mono text-sm text-subtle">
                HF {lead.scorecard.hit_factor == null ? "—" : lead.scorecard.hit_factor.toFixed(2)} &middot;{" "}
                {formatHits(lead.scorecard)}
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}
