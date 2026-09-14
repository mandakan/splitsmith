/**
 * OverviewCards -- the pipeline table at phone width (UX PR 3). One card
 * per stage with the same fields stacked: ordinal, name and audit chip on
 * the first line; footage, beep and shots on the second; the figures on
 * the third; the action row last. Multi-shooter matches show the lead
 * shooter's cell (or the filtered one); per-shooter detail is the table's
 * job on a wide screen.
 */
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import type { OverviewCell, OverviewRow } from "@/lib/overview";
import { cn } from "@/lib/utils";

import type { OverviewHrefs } from "./OverviewTable";

export interface OverviewCardsProps {
  rows: OverviewRow[];
  filterSlug: string | null;
  currentStage: number | null;
  editDenied: boolean;
  hrefs: OverviewHrefs;
  onAccept: (slug: string, stage: number) => void;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function State({ cell }: { cell: OverviewCell }) {
  if (cell.audited)
    return (
      <Chip tone="ok" tick="fire">
        audited
      </Chip>
    );
  if (cell.flagCount > 0)
    return (
      <Chip tone="warn">
        {cell.flagCount} {cell.flagCount === 1 ? "flag" : "flags"}
      </Chip>
    );
  if (cell.videoCount === 0) return <Chip tone="warn">no footage</Chip>;
  if (!cell.beepReviewed) return <Chip tone="warn">confirm beep</Chip>;
  return null;
}

function Action({
  cell,
  stage,
  primary,
  editDenied,
  hrefs,
  onAccept,
}: {
  cell: OverviewCell;
  stage: number;
  primary: boolean;
  editDenied: boolean;
  hrefs: OverviewHrefs;
  onAccept: (slug: string, stage: number) => void;
}) {
  const a = cell.action;
  const full = "flex-1 justify-center";
  switch (a.kind) {
    case "add_footage":
      return (
        <Button size="sm" className={full} asChild>
          <Link to={hrefs.footage(cell.slug)}>Add footage</Link>
        </Button>
      );
    case "confirm_beep":
      return (
        <Button size="sm" className={full} asChild>
          <Link to={hrefs.beep(cell.slug, stage)}>Confirm beep</Link>
        </Button>
      );
    case "running":
      return <span className="text-sm text-subtle">running</span>;
    case "audit":
      return (
        <>
          <Button size="sm" variant={primary ? "primary" : "default"} className={full} asChild>
            <Link to={hrefs.audit(cell.slug, stage)}>Audit</Link>
          </Button>
          {a.accept && !editDenied ? (
            <Button size="sm" variant="ghost" onClick={() => onAccept(cell.slug, stage)}>
              Accept
            </Button>
          ) : null}
        </>
      );
    case "splits":
      return (
        <Button size="sm" variant="ghost" className={full} asChild>
          <Link to={hrefs.splits(cell.slug, stage)}>Splits</Link>
        </Button>
      );
    case "none":
      return null;
  }
}

export function OverviewCards({ rows, filterSlug, currentStage, editDenied, hrefs, onAccept }: OverviewCardsProps) {
  return (
    <div className="flex flex-col gap-2.5">
      {rows.map((row) => {
        const cell = filterSlug
          ? (row.cells.find((c) => c.slug === filterSlug) ?? null)
          : row.lead;
        if (!cell) return null;
        const current = row.stageNumber === currentStage;
        const dim = cell.audited ? "text-ink" : "text-subtle";
        return (
          <section
            key={row.stageNumber}
            aria-label={`Stage ${row.stageNumber} ${row.stageName}`}
            className={cn(
              "rounded-[10px] border border-rule bg-surface px-3.5 py-3 text-md",
              current && "border-l-2 border-l-led",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate">
                <span className="mr-2 font-mono text-xs text-muted">{pad2(row.stageNumber)}</span>
                <span className="font-medium text-ink">{row.stageName}</span>
              </span>
              <State cell={cell} />
            </div>
            <div className="mt-1.5 flex flex-wrap gap-x-3.5 text-sm text-muted">
              <span>{cell.videoCount === 0 ? "no footage" : cell.videoCount === 1 ? "1 cam" : `${cell.videoCount} cams`}</span>
              {cell.beepTime != null ? <span>beep {cell.beepTime.toFixed(2)}</span> : null}
              {cell.shotCount > 0 ? <span>{cell.shotCount} shots</span> : null}
              {cell.running ? <span>detecting</span> : cell.shotCount > 0 && !cell.audited ? <span>detected</span> : null}
            </div>
            {cell.draw != null || cell.avgSplit != null || cell.timeSeconds > 0 ? (
              <div className="numeral mt-1.5 flex gap-3.5 text-md text-muted">
                {cell.draw != null ? (
                  <span>
                    draw <span className={dim}>{cell.draw.toFixed(2)}</span>
                  </span>
                ) : null}
                {cell.avgSplit != null ? (
                  <span>
                    avg <span className={dim}>{cell.avgSplit.toFixed(3)}</span>
                  </span>
                ) : null}
                {cell.timeSeconds > 0 ? (
                  <span>
                    time <span className={dim}>{cell.timeSeconds.toFixed(2)}</span>
                  </span>
                ) : null}
              </div>
            ) : null}
            {cell.action.kind !== "none" ? (
              <div className="mt-2.5 flex items-center gap-2">
                <Action
                  cell={cell}
                  stage={row.stageNumber}
                  primary={current}
                  editDenied={editDenied}
                  hrefs={hrefs}
                  onAccept={onAccept}
                />
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}
