/**
 * OverviewTable -- the match as a stage pipeline table (spec 2026-09-13
 * s4.2, UX PR 3). One row per stage: its place in the loop (footage,
 * beep, shots, audit), the splits it has produced so far (dimmed until
 * audited), and exactly one next action. On a multi-shooter match the
 * parent row shows counts and the lead shooter's figures and expands to
 * one row per shooter; `filterSlug` narrows it to one shooter.
 *
 * Everything shown is derived in lib/overview.ts; this file only maps
 * cells to primitives.
 */
import { Fragment, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import type { OverviewCell, OverviewRow } from "@/lib/overview";
import { cn } from "@/lib/utils";

export interface OverviewHrefs {
  audit: (slug: string, stage: number) => string;
  splits: (slug: string, stage: number) => string;
  footage: (slug: string) => string;
  beep: (slug: string, stage: number) => string;
}

export interface OverviewTableProps {
  rows: OverviewRow[];
  multi: boolean;
  filterSlug: string | null;
  /** Stage number of the loop's next step; rendered as the current row. */
  currentStage: number | null;
  threshold: number;
  editDenied: boolean;
  hrefs: OverviewHrefs;
  onAccept: (slug: string, stage: number) => void;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function fmt(v: number | null, digits: number): string {
  return v == null ? "\u2014" : v.toFixed(digits);
}

function FootageCell({ cell }: { cell: OverviewCell }) {
  if (cell.videoCount === 0) return <Chip tone="warn">none</Chip>;
  return <>{cell.videoCount === 1 ? "1 cam" : `${cell.videoCount} cams`}</>;
}

function BeepCell({ cell, threshold }: { cell: OverviewCell; threshold: number }) {
  if (cell.beepTime == null) return <span className="text-subtle">{"\u2014"}</span>;
  if (!cell.beepReviewed) return <Chip tone="warn">confirm</Chip>;
  const low = cell.beepConfidence != null && cell.beepConfidence < threshold;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="numeral">{cell.beepTime.toFixed(2)}</span>
      {low ? <Chip tone="warn">{Math.round(cell.beepConfidence! * 100)}%</Chip> : null}
    </span>
  );
}

function ShotsCell({ cell }: { cell: OverviewCell }) {
  if (cell.running && cell.shotCount === 0) return <span className="text-subtle">detecting&hellip;</span>;
  if (cell.shotCount === 0) return <span className="text-subtle">{"\u2014"}</span>;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="numeral">{cell.shotCount}</span>
      {cell.flagCount > 0 ? (
        <Chip tone="warn">
          {cell.flagCount} {cell.flagCount === 1 ? "flag" : "flags"}
        </Chip>
      ) : null}
    </span>
  );
}

function AuditCell({ cell }: { cell: OverviewCell }) {
  if (cell.audited)
    return (
      <Chip tone="ok" tick="fire">
        audited
      </Chip>
    );
  if (cell.status === "skipped") return <span className="text-subtle">skipped</span>;
  if (cell.running) return <span className="text-subtle">detecting&hellip;</span>;
  if (cell.shotCount > 0) return <span className="text-subtle">detected</span>;
  return <span className="text-subtle">{"\u2014"}</span>;
}

function ActionCell({
  cell,
  stage,
  primary,
  editDenied,
  hrefs,
  onAccept,
  children,
}: {
  cell: OverviewCell;
  stage: number;
  primary: boolean;
  editDenied: boolean;
  hrefs: OverviewHrefs;
  onAccept: (slug: string, stage: number) => void;
  children?: React.ReactNode;
}) {
  const a = cell.action;
  let main: React.ReactNode = null;
  switch (a.kind) {
    case "add_footage":
      main = (
        <Button size="sm" asChild>
          <Link to={hrefs.footage(cell.slug)}>Add footage</Link>
        </Button>
      );
      break;
    case "confirm_beep":
      main = (
        <Button size="sm" asChild>
          <Link to={hrefs.beep(cell.slug, stage)}>Confirm beep</Link>
        </Button>
      );
      break;
    case "running":
      main = <span className="text-subtle">running</span>;
      break;
    case "audit":
      main = (
        <>
          <Button size="sm" variant={primary ? "primary" : "default"} asChild>
            <Link to={hrefs.audit(cell.slug, stage)}>Audit</Link>
          </Button>
          {a.accept && !editDenied ? (
            <Button size="sm" variant="ghost" onClick={() => onAccept(cell.slug, stage)}>
              Accept
            </Button>
          ) : null}
        </>
      );
      break;
    case "splits":
      main = (
        <Button size="sm" variant="ghost" asChild>
          <Link to={hrefs.splits(cell.slug, stage)}>Splits</Link>
        </Button>
      );
      break;
    case "none":
      main = null;
  }
  return (
    <span className="inline-flex items-center justify-end gap-1.5">
      {main}
      {children}
    </span>
  );
}

function CellRow({
  row,
  cell,
  current,
  primary,
  props,
  child,
  toggle,
}: {
  row: OverviewRow;
  cell: OverviewCell;
  current: boolean;
  primary: boolean;
  props: OverviewTableProps;
  child?: boolean;
  toggle?: React.ReactNode;
}) {
  const dim = !cell.audited;
  return (
    <Tr current={current} className={cn(child && "bg-bg")}>
      <Td kind="ordinal">{child ? "" : pad2(row.stageNumber)}</Td>
      <Td kind="name" className={cn(child && "pl-7 font-normal text-ink-2")}>
        {child ? cell.shooterName : row.stageName}
      </Td>
      <Td>
        <FootageCell cell={cell} />
      </Td>
      <Td>
        <BeepCell cell={cell} threshold={props.threshold} />
      </Td>
      <Td>
        <ShotsCell cell={cell} />
      </Td>
      <Td>
        <AuditCell cell={cell} />
      </Td>
      <Td kind="num" dim={dim}>
        {fmt(cell.draw, 2)}
      </Td>
      <Td kind="num" dim={dim}>
        {fmt(cell.avgSplit, 3)}
      </Td>
      <Td kind="num" dim={dim}>
        {cell.timeSeconds > 0 ? cell.timeSeconds.toFixed(2) : "\u2014"}
      </Td>
      <Td className="text-right">
        <ActionCell
          cell={cell}
          stage={row.stageNumber}
          primary={primary}
          editDenied={props.editDenied}
          hrefs={props.hrefs}
          onAccept={props.onAccept}
        >
          {toggle}
        </ActionCell>
      </Td>
    </Tr>
  );
}

function ParentRow({
  row,
  current,
  open,
  onToggle,
}: {
  row: OverviewRow;
  current: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const n = row.cells.length;
  const count = (pred: (c: OverviewCell) => boolean) => `${row.cells.filter(pred).length} / ${n}`;
  const lead = row.lead;
  const dim = !(lead?.audited ?? false);
  return (
    <Tr current={current}>
      <Td kind="ordinal">{pad2(row.stageNumber)}</Td>
      <Td kind="name">{row.stageName}</Td>
      <Td>{count((c) => c.videoCount > 0)}</Td>
      <Td>{count((c) => c.beepReviewed)}</Td>
      <Td>{count((c) => c.shotCount > 0)}</Td>
      <Td>
        {row.auditedCount === n ? (
          <Chip tone="ok" tick="fire">
            {row.auditedCount} audited
          </Chip>
        ) : (
          <Chip tone="warn">{`${row.auditedCount} / ${n} audited`}</Chip>
        )}
      </Td>
      <Td kind="num" dim={dim}>
        {fmt(lead?.draw ?? null, 2)}
      </Td>
      <Td kind="num" dim={dim}>
        {fmt(lead?.avgSplit ?? null, 3)}
      </Td>
      <Td kind="num" dim={dim}>
        {lead && lead.timeSeconds > 0 ? lead.timeSeconds.toFixed(2) : "\u2014"}
      </Td>
      <Td className="text-right">
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={open}
          aria-label={open ? "Hide shooters" : "Show shooters"}
          onClick={onToggle}
        >
          {open ? <ChevronUp /> : <ChevronDown />}
        </Button>
      </Td>
    </Tr>
  );
}

export function OverviewTable(props: OverviewTableProps) {
  const { rows, multi, filterSlug, currentStage } = props;
  const [open, setOpen] = useState<Set<number>>(() => new Set());
  const toggle = (n: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  // The one primary action on the page: the current row's lead cell (or
  // the first cell whose action is audit / confirm on a multi row).
  const primaryKey = (() => {
    const row = rows.find((r) => r.stageNumber === currentStage);
    if (!row) return null;
    const cell =
      row.cells.find(
        (c) =>
          (filterSlug == null || c.slug === filterSlug) &&
          (c.action.kind === "audit" || c.action.kind === "confirm_beep"),
      ) ?? null;
    return cell ? `${row.stageNumber}:${cell.slug}` : null;
  })();
  const expanded = multi && filterSlug == null;

  return (
    <Table>
      <thead>
        <tr>
          <Th>#</Th>
          <Th>Stage</Th>
          <Th>Footage</Th>
          <Th>Beep</Th>
          <Th>Shots</Th>
          <Th>Audit</Th>
          <Th align="right">Draw</Th>
          <Th align="right">Avg split</Th>
          <Th align="right">Time</Th>
          <Th />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const current = row.stageNumber === currentStage;
          if (!expanded) {
            const cell = filterSlug
              ? (row.cells.find((c) => c.slug === filterSlug) ?? null)
              : row.lead;
            if (!cell) return null;
            return (
              <CellRow
                key={row.stageNumber}
                row={row}
                cell={cell}
                current={current}
                primary={primaryKey === `${row.stageNumber}:${cell.slug}`}
                props={props}
              />
            );
          }
          const isOpen = open.has(row.stageNumber);
          return (
            <Fragment key={row.stageNumber}>
              <ParentRow
                row={row}
                current={current}
                open={isOpen}
                onToggle={() => toggle(row.stageNumber)}
              />
              {isOpen
                ? row.cells.map((cell) => (
                    <CellRow
                      key={`${row.stageNumber}:${cell.slug}`}
                      row={row}
                      cell={cell}
                      current={false}
                      primary={primaryKey === `${row.stageNumber}:${cell.slug}`}
                      props={props}
                      child
                    />
                  ))
                : null}
            </Fragment>
          );
        })}
      </tbody>
    </Table>
  );
}
