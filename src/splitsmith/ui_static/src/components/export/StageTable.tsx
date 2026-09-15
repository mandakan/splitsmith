/**
 * The Export page's stage picker (spec 2026-09-13 s4.8): one row per
 * stage with a checkbox, the ordinal, the name, time, shots and either a
 * Ready chip or the one-line reason it cannot export with its fix as a
 * link. Rows without footage past the first few collapse to one line.
 */
import { useState } from "react";
import { Link } from "react-router-dom";

import { Chip } from "@/components/ui/Chip";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import type { ExportStageRow, FixTarget } from "@/lib/exportPlan";
import { cn } from "@/lib/utils";

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

const COLLAPSE_AFTER = 3;

export interface StageTableProps {
  rows: ExportStageRow[];
  selected: Set<number>;
  onToggle: (stageNumber: number) => void;
  /** Where each fix target links. */
  fixHref: (to: FixTarget, stageNumber: number) => string;
}

export function StageTable({ rows, selected, onToggle, fixHref }: StageTableProps) {
  const [expanded, setExpanded] = useState(false);
  // Stages with no footage at all are the long tail on a half-shot match;
  // show the first few and fold the rest into one row.
  const noFootage = rows.filter((r) => r.block?.reason === "No footage");
  const folded = !expanded && noFootage.length > COLLAPSE_AFTER ? new Set(noFootage.slice(COLLAPSE_AFTER).map((r) => r.stage.stage_number)) : new Set<number>();
  const visible = rows.filter((r) => !folded.has(r.stage.stage_number));
  return (
    <Table>
      <thead>
        <tr>
          <Th className="w-8" />
          <Th className="w-8">#</Th>
          <Th>Stage</Th>
          <Th align="right">Time</Th>
          <Th align="right">Shots</Th>
          <Th>Status</Th>
        </tr>
      </thead>
      <tbody>
        {visible.map((r) => {
          const n = r.stage.stage_number;
          const on = selected.has(n);
          return (
            <Tr key={n} className={cn(!r.eligible && "text-subtle")}>
              <Td className="w-8">
                <input
                  type="checkbox"
                  aria-label={`Select stage ${n} ${r.stage.stage_name}`}
                  checked={on}
                  disabled={!r.eligible}
                  onChange={() => onToggle(n)}
                  className="accent-[var(--color-ink)] disabled:opacity-35"
                />
              </Td>
              <Td kind="ordinal">{pad2(n)}</Td>
              <Td kind="name" className={cn(!r.eligible && "font-normal text-muted")}>
                {r.stage.stage_name}
              </Td>
              <Td kind="num" dim={!r.eligible}>
                {r.time !== null ? r.time.toFixed(2) : "—"}
              </Td>
              <Td kind="num" dim={!r.eligible}>
                {r.shots !== null ? r.shots : "—"}
              </Td>
              <Td>
                {r.block === null && r.bare ? (
                  <span className="inline-flex items-center gap-2 text-sm text-muted">
                    <Chip title="Renders from the beep and the stage time; no shot markers, overlay or captions">
                      No splits
                    </Chip>
                    <Link to={fixHref("audit", n)} className="text-ink-2 underline underline-offset-4 hover:text-ink">
                      Audit
                    </Link>
                  </span>
                ) : r.block === null ? (
                  <Chip tone="ok" tick="fire">
                    Ready
                  </Chip>
                ) : (
                  <span className="inline-flex flex-wrap items-center gap-2 text-sm text-muted">
                    {r.block.warn ? (
                      <Chip tone="warn" tick="reload">
                        {r.block.reason.split(" -- ")[0]}
                      </Chip>
                    ) : null}
                    <span>{r.block.warn ? r.block.reason.split(" -- ").slice(1).join(" -- ") : r.block.reason}</span>
                    {r.block.fix ? (
                      <Link
                        to={fixHref(r.block.fix.to, n)}
                        className="text-ink-2 underline underline-offset-4 hover:text-ink"
                      >
                        {r.block.fix.label}
                      </Link>
                    ) : null}
                  </span>
                )}
              </Td>
            </Tr>
          );
        })}
        {folded.size > 0 ? (
          <Tr>
            <Td colSpan={6} className="text-center text-sm text-muted">
              <button type="button" onClick={() => setExpanded(true)} className="underline underline-offset-4 hover:text-ink">
                {folded.size} more {folded.size === 1 ? "stage" : "stages"} without footage
              </button>
            </Td>
          </Tr>
        ) : null}
      </tbody>
    </Table>
  );
}
