/**
 * ShotList -- the Audit page's right-column list (UX PR 5, spec s4.4):
 * flagged shots first, then every marker in time order with rejected
 * candidates kept in place, struck through and dim. Tap a row to jump
 * the playhead. The current row is surface-2 with the led inset
 * (current position, no glow). Shaped like the stage page's shot table.
 */
import { useEffect, useRef } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import { Label } from "@/components/ui/Label";
import type { ShotRow } from "@/lib/auditStep";
import { cn } from "@/lib/utils";

export interface ShotListProps {
  rows: { flagged: ShotRow[]; all: ShotRow[] };
  currentMarkerId: string | null;
  onJump: (marker: AuditMarker) => void;
}

const GRID = "grid grid-cols-[30px_54px_58px_44px_minmax(0,1fr)] items-center gap-2";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function Row({ row, split, current, onJump }: { row: ShotRow; split: number | null; current: boolean; onJump: () => void }) {
  return (
    <button
      type="button"
      data-marker-id={row.marker.id}
      onClick={onJump}
      className={cn(
        GRID,
        "numeral w-full border-b border-rule px-3 py-1.5 text-left text-sm last:border-b-0 transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led",
        row.rejected ? "text-subtle" : "text-ink-2",
        current && "bg-surface-2 shadow-[inset_2px_0_0_var(--color-led)]",
      )}
    >
      <span className="text-muted">{row.index != null ? pad2(row.index) : "·"}</span>
      <span className="text-right">{row.marker.time.toFixed(2)}</span>
      <span className={cn("text-right", row.rejected ? "line-through" : "font-medium text-ink")}>
        {split != null ? split.toFixed(3) : "—"}
      </span>
      <span className="text-right text-muted">
        {row.marker.confidence != null ? row.marker.confidence.toFixed(2) : "—"}
      </span>
      <span className="truncate font-sans" title={row.flag ?? undefined}>
        {row.flag ? (
          <span className="text-live">
            <i aria-hidden className="mr-1.5 inline-block size-1.5 rounded-full bg-live align-middle" />
            {row.flag}
          </span>
        ) : row.rejected ? (
          <span className="text-subtle">rejected</span>
        ) : null}
      </span>
    </button>
  );
}

export function ShotList({ rows, currentMarkerId, onJump }: ShotListProps) {
  const listRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!currentMarkerId) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-marker-id="${currentMarkerId}"]`);
    // jsdom has no scrollIntoView; the guard keeps the tests honest.
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
  }, [currentMarkerId]);

  // Split = time since the previous kept shot; the first kept shot's is
  // its time from the beep (the draw), matching the current-shot line.
  const splitById = new Map<string, number | null>();
  let prev = 0;
  for (const r of rows.all) {
    splitById.set(r.marker.id, r.marker.time - prev);
    if (!r.rejected) prev = r.marker.time;
  }

  return (
    <section aria-label="Shots" className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className={cn(GRID, "border-b border-rule-strong px-3 py-2")}>
        <Label>#</Label>
        <Label className="text-right">T</Label>
        <Label className="text-right">Split</Label>
        <Label className="text-right">Conf</Label>
        <span />
      </div>
      <div ref={listRef} className="max-h-[60vh] overflow-y-auto">
        {rows.flagged.length > 0 ? (
          <>
            <div className="border-b border-rule bg-surface-2 px-3 py-1.5">
              <Label tone="live">Flagged &middot; {rows.flagged.length}</Label>
            </div>
            {rows.flagged.map((row) => (
              <Row
                key={`f-${row.marker.id}`}
                row={row}
                split={splitById.get(row.marker.id) ?? null}
                current={row.marker.id === currentMarkerId}
                onJump={() => onJump(row.marker)}
              />
            ))}
          </>
        ) : null}
        <div className="border-b border-rule bg-surface-2 px-3 py-1.5">
          <Label tone="subtle">All shots &middot; {rows.all.filter((r) => !r.rejected).length}</Label>
        </div>
        {rows.all.map((row) => (
          <Row
            key={row.marker.id}
            row={row}
            split={splitById.get(row.marker.id) ?? null}
            current={row.marker.id === currentMarkerId}
            onJump={() => onJump(row.marker)}
          />
        ))}
      </div>
    </section>
  );
}
