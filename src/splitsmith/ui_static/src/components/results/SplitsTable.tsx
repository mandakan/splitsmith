/**
 * SplitsTable -- the Splits page's desktop table (UX PR 4, spec s4.5).
 * One row per stage: ordinal, name, then the splits the audit produced
 * (draw, avg split, fastest, shots), the scoreboard time, and the
 * scorecard's HF and hits dimmed on the right. A row with nothing to
 * play carries no play glyph, so the last column reads "what can I
 * watch". Owner not-audited rows say so and link to Audit; runs with no
 * footage (owner) or no video (share) collapse into one line.
 *
 * With several shooters showing, a stage is a group: its ordinal and name
 * span one row per shooter. The column headers sort the shooters inside
 * every stage (the stages never move), and the best value of each column
 * in a stage is marked unless the viewer turned the marks off.
 */
import { Fragment } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Play } from "lucide-react";
import { Link } from "react-router-dom";

import { StageCompareLink } from "@/components/match/StageCompareLink";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import { formatClock } from "@/lib/overview";
import {
  bestCells,
  formatHits,
  sortCells,
  type ScoreboardTotals,
  type SplitsCell,
  type SplitsRow,
  type SplitsSort,
  type SplitsSortKey,
} from "@/lib/splitsTable";
import { cn } from "@/lib/utils";

export interface SplitsHrefs {
  stage: (slug: string, n: number) => string;
  audit: (slug: string, n: number) => string;
}

export interface SplitsTableProps {
  rows: SplitsRow[];
  /** Several shooters showing: one group of shooter rows per stage. */
  grouped: boolean;
  share: boolean;
  totals: ScoreboardTotals | null;
  hrefs: SplitsHrefs;
  sort: SplitsSort | null;
  onSort: (key: SplitsSortKey) => void;
  /** Mark each column's best value per stage (grouped only). */
  markBest: boolean;
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
export function PlayLink({
  to,
  label,
  className,
}: {
  to: string;
  label: string;
  className?: string;
}) {
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

type Best = Partial<Record<SplitsSortKey, Set<string>>>;

/** A marked figure: the stage's best in its column. Green is the app's
 *  "quick" hue (the split tiers on the stage page). */
function mark(
  best: Best | null,
  key: SplitsSortKey,
  slug: string,
): string | undefined {
  return best?.[key]?.has(slug) ? "font-semibold text-done" : undefined;
}

/** The four splits cells plus time, or one "not audited" / "no video"
 *  cell spanning them. */
function FigureCells({
  cell,
  n,
  share,
  hrefs,
  best,
}: {
  cell: SplitsCell;
  n: number;
  share: boolean;
  hrefs: SplitsHrefs;
  best: Best | null;
}) {
  if (!cell.audited) {
    return (
      <Td colSpan={5} dim>
        {share ? (
          "no video"
        ) : (
          <>
            not audited
            {" · "}
            <Link
              to={hrefs.audit(cell.slug, n)}
              className="text-ink-2 hover:text-ink"
            >
              Audit
            </Link>
          </>
        )}
      </Td>
    );
  }
  return (
    <>
      <Td kind="num" className={mark(best, "draw", cell.slug)}>
        {fmt(cell.draw, 2)}
      </Td>
      <Td kind="num" className={mark(best, "avgSplit", cell.slug)}>
        {fmt(cell.avgSplit, 3)}
      </Td>
      <Td kind="num" className={mark(best, "fastest", cell.slug)}>
        {fmt(cell.fastestSplit, 3)}
      </Td>
      <Td kind="num">{cell.shotCount}</Td>
      <Td kind="num" className={mark(best, "time", cell.slug)}>
        {cell.timeSeconds > 0 ? formatClock(cell.timeSeconds) : DASH}
      </Td>
    </>
  );
}

function ScoreCells({
  cell,
  best,
}: {
  cell: SplitsCell | null;
  best?: Best | null;
}) {
  const sc = cell?.scorecard ?? null;
  const marked = cell ? mark(best ?? null, "hf", cell.slug) : undefined;
  return (
    <>
      <Td kind="num" dim={!marked} className={marked}>
        {fmt(sc?.hit_factor ?? null, 2)}
      </Td>
      <Td dim className="whitespace-nowrap">
        {sc ? formatHits(sc) : DASH}
      </Td>
    </>
  );
}

/** A sortable column header: the label is the button; aria-sort says the
 *  direction while it is the sort. */
function SortTh({
  label,
  sortKey,
  sort,
  onSort,
  className,
}: {
  label: string;
  sortKey: SplitsSortKey;
  sort: SplitsSort | null;
  onSort: (key: SplitsSortKey) => void;
  className?: string;
}) {
  const on = sort?.key === sortKey ? sort.dir : null;
  const Icon = on === "asc" ? ArrowUp : on === "desc" ? ArrowDown : ArrowUpDown;
  return (
    <Th
      align="right"
      aria-sort={
        on === "asc" ? "ascending" : on === "desc" ? "descending" : "none"
      }
      className={className}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "inline-flex items-center gap-1 uppercase transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
          on ? "text-ink" : undefined,
        )}
      >
        {label}
        <Icon
          aria-hidden
          className={cn("size-3", on ? "text-ink" : "text-subtle")}
        />
      </button>
    </Th>
  );
}

function ScoreboardRow({
  totals,
  lead,
}: {
  totals: ScoreboardTotals;
  lead: number;
}) {
  return (
    <Tr className="border-t border-rule-strong">
      <Td colSpan={lead} className="font-mono text-sm text-muted">
        Scoreboard
      </Td>
      <Td colSpan={4} />
      <Td kind="num" className="text-sm text-ink-2">
        {formatClock(totals.time)}
      </Td>
      <Td kind="num" className="text-sm text-ink-2">
        {fmt(totals.hitFactor, 2)}
      </Td>
      <Td className="whitespace-nowrap font-mono text-sm text-muted">
        {totals.alphas}A {totals.charlies}C {totals.deltas}D
        {totals.misses > 0 ? ` ${totals.misses}M` : ""}
      </Td>
      <Td />
    </Tr>
  );
}

function CollapsedRow({
  row,
  lead,
}: {
  row: Extract<SplitsRow, { kind: "collapsed" }>;
  lead: number;
}) {
  return (
    <Tr>
      <Td kind="ordinal" className="whitespace-nowrap">
        {row.count === 1 ? pad2(row.from) : `${pad2(row.from)}–${pad2(row.to)}`}
      </Td>
      <Td dim colSpan={lead - 1}>
        {row.count === 1
          ? row.firstName
          : `${row.firstName} to ${row.lastName}`}
      </Td>
      <Td colSpan={5} dim>
        {row.reason === "no_video" ? "no video" : "no footage"}
      </Td>
      <ScoreCells cell={null} />
      <Td />
    </Tr>
  );
}

export function SplitsTable({
  rows,
  grouped,
  share,
  totals,
  hrefs,
  sort,
  onSort,
  markBest,
}: SplitsTableProps) {
  // Columns before the figures: #, Stage, and Shooter when grouped.
  const lead = grouped ? 3 : 2;
  const sortable = grouped;
  const header = (label: string, key: SplitsSortKey, className?: string) =>
    sortable ? (
      <SortTh
        label={label}
        sortKey={key}
        sort={sort}
        onSort={onSort}
        className={className}
      />
    ) : (
      <Th align="right" className={className}>
        {label}
      </Th>
    );

  return (
    <Table aria-label="Splits by stage">
      <thead>
        <tr>
          <Th>#</Th>
          <Th>Stage</Th>
          {grouped ? <Th>Shooter</Th> : null}
          {header("Draw", "draw")}
          {header("Avg split", "avgSplit")}
          {header("Fastest", "fastest")}
          {header("Shots", "shots")}
          {header("Time", "time")}
          {header("HF", "hf", "text-subtle")}
          <Th className="text-subtle">Hits</Th>
          <Th aria-label="Play" />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          if (row.kind === "collapsed")
            return <CollapsedRow key={`c${row.from}`} row={row} lead={lead} />;
          const { stageNumber: n } = row;
          if (!grouped) {
            const cell = row.lead;
            return (
              <Tr key={n}>
                <Td kind="ordinal">{pad2(n)}</Td>
                <Td kind={cell?.audited ? "name" : "text"} dim={!cell?.audited}>
                  {row.stageName}
                </Td>
                {cell ? (
                  <FigureCells
                    cell={cell}
                    n={n}
                    share={share}
                    hrefs={hrefs}
                    best={null}
                  />
                ) : (
                  <Td colSpan={5} dim>
                    {share ? "no video" : "not audited"}
                  </Td>
                )}
                <ScoreCells cell={cell} />
                <Td className="text-right">
                  {cell?.audited ? (
                    <PlayLink
                      to={hrefs.stage(cell.slug, n)}
                      label={`Play stage ${n}`}
                    />
                  ) : null}
                </Td>
              </Tr>
            );
          }
          const cells = sortCells(row.cells, sort);
          const best = markBest ? bestCells(row.cells) : null;
          return (
            <Fragment key={n}>
              {cells.map((cell, i) => (
                <Tr
                  key={`${n}:${cell.slug}`}
                  className={cn(
                    i < cells.length - 1
                      ? "border-b-rule/40"
                      : "border-b-rule-strong",
                  )}
                >
                  {i === 0 ? (
                    <>
                      <Td
                        kind="ordinal"
                        rowSpan={cells.length}
                        className="align-top"
                      >
                        {pad2(n)}
                      </Td>
                      <Td rowSpan={cells.length} className="align-top">
                        <span className="flex flex-col items-start gap-1.5">
                          <span className="font-medium text-ink">
                            {row.stageName}
                          </span>
                          <StageCompareLink
                            stageNumber={n}
                            comparableCount={row.auditedCount}
                          />
                        </span>
                      </Td>
                    </>
                  ) : null}
                  <Td
                    kind={cell.audited ? "name" : "text"}
                    dim={!cell.audited}
                    className="whitespace-nowrap"
                  >
                    {cell.shooterName}
                  </Td>
                  <FigureCells
                    cell={cell}
                    n={n}
                    share={share}
                    hrefs={hrefs}
                    best={best}
                  />
                  <ScoreCells cell={cell} best={best} />
                  <Td className="text-right">
                    {cell.audited ? (
                      <PlayLink
                        to={hrefs.stage(cell.slug, n)}
                        label={`Play stage ${n} ${cell.shooterName}`}
                      />
                    ) : null}
                  </Td>
                </Tr>
              ))}
            </Fragment>
          );
        })}
        {totals ? <ScoreboardRow totals={totals} lead={lead} /> : null}
      </tbody>
    </Table>
  );
}
