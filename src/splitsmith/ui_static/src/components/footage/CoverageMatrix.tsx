/**
 * CoverageMatrix -- the Footage page's table (UX PR 6, spec s4.3): one
 * row per stage, one cell per shooter with its file chips, the primary's
 * beep state, and a row menu. A cell with no footage says so and offers
 * Assign. Single-shooter matches carry the beep as its own column;
 * multi-shooter matches put it under each cell's chips.
 */
import { useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import type { StageVideo } from "@/lib/api";
import { beepState, type FootageCell, type FootageRow } from "@/lib/footage";
import { cn } from "@/lib/utils";

import { FileChip } from "./FileChip";

export interface FootageHrefs {
  audit: (slug: string, stage: number) => string;
}

export interface CoverageMatrixProps {
  rows: FootageRow[];
  currentVideoId: string | null;
  currentStage: number | null;
  hrefs: FootageHrefs;
  onOpen: (slug: string, stage: number, video: StageVideo) => void;
  onAssign: (slug: string, stage: number) => void;
  onDetectBeep: (slug: string, stage: number) => void;
  editDenied: boolean;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function BeepCell({ cell, stage, hrefs, className }: { cell: FootageCell; stage: number; hrefs: FootageHrefs; className?: string }) {
  const b = beepState(cell);
  return (
    <span className={cn("numeral inline-flex items-center gap-2 whitespace-nowrap", className)}>
      <span className={b.tone === "warn" ? "text-live" : b.tone === "ok" ? "text-ink" : "text-subtle"}>
        {b.label}
        {b.tone === "ok" ? <span className="ml-1 text-done">&#10003;</span> : null}
      </span>
      {b.confirmable ? (
        <Button size="sm" asChild>
          <Link to={hrefs.audit(cell.slug, stage)}>Confirm</Link>
        </Button>
      ) : null}
    </span>
  );
}

function Cell({ cell, stage, current, multi, hrefs, onOpen, onAssign, editDenied }: {
  cell: FootageCell;
  stage: number;
  current: string | null;
  multi: boolean;
  hrefs: FootageHrefs;
  onOpen: CoverageMatrixProps["onOpen"];
  onAssign: CoverageMatrixProps["onAssign"];
  editDenied: boolean;
}) {
  if (cell.videos.length === 0) {
    return (
      <Td dim className="whitespace-nowrap">
        no footage
        {editDenied ? null : (
          <>
            {" · "}
            <button type="button" onClick={() => onAssign(cell.slug, stage)} className="text-ink-2 hover:text-ink">
              Assign&hellip;
            </button>
          </>
        )}
      </Td>
    );
  }
  return (
    <Td>
      <span className="flex flex-wrap gap-1.5">
        {cell.videos.map((v) => (
          <FileChip key={v.video_id} video={v} current={v.video_id === current} onOpen={(video) => onOpen(cell.slug, stage, video)} />
        ))}
      </span>
      {multi ? <BeepCell cell={cell} stage={stage} hrefs={hrefs} className="mt-1 text-sm" /> : null}
    </Td>
  );
}

export function CoverageMatrix(props: CoverageMatrixProps) {
  const { rows, currentVideoId, currentStage, hrefs, onOpen, onAssign, onDetectBeep, editDenied } = props;
  const [menuFor, setMenuFor] = useState<number | null>(null);
  const shooters = rows[0]?.cells ?? [];
  const multi = shooters.length > 1;
  return (
    <Table aria-label="Coverage by stage and shooter">
      <thead>
        <tr>
          <Th>#</Th>
          <Th>Stage</Th>
          {shooters.map((c) => (
            <Th key={c.slug}>{c.shooterName}</Th>
          ))}
          {multi ? null : <Th align="right">Beep</Th>}
          <Th aria-label="Actions" />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const n = row.stageNumber;
          const first = row.cells[0] ?? null;
          return (
            <Tr key={n} current={n === currentStage}>
              <Td kind="ordinal">{pad2(n)}</Td>
              <Td kind={row.covered ? "name" : "text"} dim={!row.covered} className="whitespace-nowrap">
                {row.stageName}
              </Td>
              {row.cells.map((cell) => (
                <Cell
                  key={cell.slug}
                  cell={cell}
                  stage={n}
                  current={currentVideoId}
                  multi={multi}
                  hrefs={hrefs}
                  onOpen={onOpen}
                  onAssign={onAssign}
                  editDenied={editDenied}
                />
              ))}
              {multi ? null : (
                <Td className="text-right">{first ? <BeepCell cell={first} stage={n} hrefs={hrefs} className="justify-end" /> : null}</Td>
              )}
              <Td className="relative text-right">
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Stage ${n} actions`}
                  aria-haspopup="menu"
                  aria-expanded={menuFor === n}
                  onClick={() => setMenuFor((v) => (v === n ? null : n))}
                >
                  <MoreHorizontal className="size-4" aria-hidden />
                </Button>
                <Menu open={menuFor === n} onClose={() => setMenuFor(null)} align="right">
                  {row.cells.map((cell) => (
                    <span key={cell.slug} className="contents">
                      {multi ? <span className="px-2.5 pt-1 text-sm text-muted">{cell.shooterName}</span> : null}
                      {editDenied ? null : (
                        <button
                          type="button"
                          role="menuitem"
                          className={menuItemClass}
                          onClick={() => {
                            setMenuFor(null);
                            onAssign(cell.slug, n);
                          }}
                        >
                          Assign a video&hellip;
                        </button>
                      )}
                      {cell.primary && !editDenied ? (
                        <button
                          type="button"
                          role="menuitem"
                          className={menuItemClass}
                          onClick={() => {
                            setMenuFor(null);
                            onDetectBeep(cell.slug, n);
                          }}
                        >
                          Detect beep
                        </button>
                      ) : null}
                      {cell.primary ? (
                        <Link role="menuitem" className={menuItemClass} to={hrefs.audit(cell.slug, n)}>
                          Open in Audit
                        </Link>
                      ) : null}
                    </span>
                  ))}
                </Menu>
              </Td>
            </Tr>
          );
        })}
      </tbody>
    </Table>
  );
}
