/**
 * SplitsTable -- the Splits page's desktop table (UX PR 4, spec s4.5).
 * One row per stage: ordinal, name, then the splits the audit produced
 * (draw, avg split, fastest, shots), the scoreboard time, and the
 * scorecard's HF and hits dimmed on the right. A row with nothing to
 * play carries no play glyph, so the last column reads "what can I
 * watch". Owner not-audited rows say so and link to Audit; runs with no
 * footage (owner) or no video (share) collapse into one line. Multi-
 * shooter matches show the lead shooter's figures with a disclosure to
 * one sub-row per shooter.
 */
import { Fragment, useState } from "react";
import { ChevronDown, ChevronUp, Play } from "lucide-react";
import { Link } from "react-router-dom";

import { StageCompareLink } from "@/components/match/StageCompareLink";
import { Button } from "@/components/ui/button";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import { formatClock } from "@/lib/overview";
import { formatHits, type ScoreboardTotals, type SplitsCell, type SplitsRow } from "@/lib/splitsTable";
import { cn } from "@/lib/utils";

export interface SplitsHrefs {
  stage: (slug: string, n: number) => string;
  audit: (slug: string, n: number) => string;
}

export interface SplitsTableProps {
  rows: SplitsRow[];
  multi: boolean;
  filterSlug: string | null;
  share: boolean;
  totals: ScoreboardTotals | null;
  hrefs: SplitsHrefs;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function fmt(v: number | null, digits: number): string {
  return v == null ? "—" : v.toFixed(digits);
}

const DASH = "—";

/** Play affordance: muted at rest, ink on hover; the row's link into
 *  the stage page. */
export function PlayLink({ to, label, className }: { to: string; label: string; className?: string }) {
  return (
    <Link
      to={to}
      aria-label={label}
      className={cn(
        "inline-flex size-7 items-center justify-center rounded-full border border-rule-strong text-ink-2 transition-colors hover:border-ink-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
        className,
      )}
    >
      <Play className="size-3 fill-current" aria-hidden />
    </Link>
  );
}

/** The four splits cells plus time, or one "not audited" / "no video"
 *  cell spanning them. */
function FigureCells({ cell, n, share, hrefs }: { cell: SplitsCell; n: number; share: boolean; hrefs: SplitsHrefs }) {
  if (!cell.audited) {
    return (
      <Td colSpan={5} dim>
        {share ? (
          "no video"
        ) : (
          <>
            not audited
            {" · "}
            <Link to={hrefs.audit(cell.slug, n)} className="text-ink-2 hover:text-ink">
              Audit
            </Link>
          </>
        )}
      </Td>
    );
  }
  return (
    <>
      <Td kind="num">{fmt(cell.draw, 2)}</Td>
      <Td kind="num">{fmt(cell.avgSplit, 3)}</Td>
      <Td kind="num">{fmt(cell.fastestSplit, 3)}</Td>
      <Td kind="num">{cell.shotCount}</Td>
      <Td kind="num">{cell.timeSeconds > 0 ? formatClock(cell.timeSeconds) : DASH}</Td>
    </>
  );
}

function ScoreCells({ cell }: { cell: SplitsCell | null }) {
  const sc = cell?.scorecard ?? null;
  return (
    <>
      <Td kind="num" dim>
        {fmt(sc?.hit_factor ?? null, 2)}
      </Td>
      <Td dim className="whitespace-nowrap">
        {sc ? formatHits(sc) : DASH}
      </Td>
    </>
  );
}

export function SplitsTable({ rows, multi, filterSlug, share, totals, hrefs }: SplitsTableProps) {
  const [open, setOpen] = useState<Set<number>>(() => new Set());
  const toggle = (n: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  const expandable = multi && filterSlug == null;

  return (
    <Table aria-label="Splits by stage">
      <thead>
        <tr>
          <Th>#</Th>
          <Th>Stage</Th>
          <Th align="right">Draw</Th>
          <Th align="right">Avg split</Th>
          <Th align="right">Fastest</Th>
          <Th align="right">Shots</Th>
          <Th align="right">Time</Th>
          <Th align="right" className="text-subtle">
            HF
          </Th>
          <Th className="text-subtle">Hits</Th>
          <Th aria-label="Play" />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          if (row.kind === "collapsed") {
            return (
              <Tr key={`c${row.from}`}>
                <Td kind="ordinal" className="whitespace-nowrap">
                  {row.count === 1 ? pad2(row.from) : `${pad2(row.from)}–${pad2(row.to)}`}
                </Td>
                <Td dim>{row.count === 1 ? row.firstName : `${row.firstName} to ${row.lastName}`}</Td>
                <Td colSpan={5} dim>
                  {row.reason === "no_video" ? "no video" : "no footage"}
                </Td>
                <ScoreCells cell={null} />
                <Td />
              </Tr>
            );
          }
          const { stageNumber: n, lead } = row;
          const isOpen = open.has(n);
          return (
            <Fragment key={n}>
              <Tr>
                <Td kind="ordinal">{pad2(n)}</Td>
                <Td kind={lead?.audited ? "name" : "text"} dim={!lead?.audited}>
                  <span className="inline-flex items-center gap-2">
                    {row.stageName}
                    {expandable ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-expanded={isOpen}
                        aria-label={isOpen ? "Hide shooters" : "Show shooters"}
                        onClick={() => toggle(n)}
                      >
                        {isOpen ? <ChevronUp /> : <ChevronDown />}
                        <span className="numeral text-muted">{row.auditedCount}</span>
                      </Button>
                    ) : null}
                    {multi ? <StageCompareLink stageNumber={n} comparableCount={row.auditedCount} /> : null}
                  </span>
                </Td>
                {lead ? (
                  <FigureCells cell={lead} n={n} share={share} hrefs={hrefs} />
                ) : (
                  <Td colSpan={5} dim>
                    {share ? "no video" : "not audited"}
                  </Td>
                )}
                <ScoreCells cell={lead} />
                <Td className="text-right">
                  {lead?.audited ? (
                    <PlayLink to={hrefs.stage(lead.slug, n)} label={`Play stage ${n}`} />
                  ) : null}
                </Td>
              </Tr>
              {expandable && isOpen
                ? row.cells.map((cell) => (
                    <Tr key={`${n}:${cell.slug}`} className="bg-surface-2">
                      <Td kind="ordinal" />
                      <Td dim={!cell.audited}>{cell.shooterName}</Td>
                      <FigureCells cell={cell} n={n} share={share} hrefs={hrefs} />
                      <ScoreCells cell={cell} />
                      <Td className="text-right">
                        {cell.audited ? (
                          <PlayLink to={hrefs.stage(cell.slug, n)} label={`Play stage ${n} ${cell.shooterName}`} />
                        ) : null}
                      </Td>
                    </Tr>
                  ))
                : null}
            </Fragment>
          );
        })}
        {totals ? (
          <Tr className="border-t border-rule-strong">
            <Td />
            <Td className="font-mono text-sm text-muted">Scoreboard</Td>
            <Td colSpan={4} />
            <Td kind="num" className="text-sm text-ink-2">
              {formatClock(totals.time)}
            </Td>
            <Td kind="num" className="text-sm text-ink-2">
              {fmt(totals.hitFactor, 2)}
            </Td>
            <Td className="whitespace-nowrap font-mono text-sm text-muted">
              {totals.alphas}A {totals.charlies}C {totals.deltas}D{totals.misses > 0 ? ` ${totals.misses}M` : ""}
            </Td>
            <Td />
          </Tr>
        ) : null}
      </tbody>
    </Table>
  );
}
