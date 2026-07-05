/**
 * Right-side drawer with the full marker list (#15).
 *
 * Hidden by default; press `L` to toggle. The drawer shows every marker
 * (detected, rejected, manual) so the user can see candidates that were
 * filtered out alongside the kept shots. Click a row to jump the playhead
 * to that marker; click again to close. Sorted columns: time, kind,
 * confidence, peak amplitude, candidate #.
 *
 * The drawer floats over the right edge of the audit Card via fixed
 * positioning. We don't pull in shadcn's Sheet primitive -- a tiny
 * translate-x animation is enough for a localhost-only single-pane SPA.
 */

import { useRef, useState } from "react";
import { ArrowDown, ArrowUp, Trash2, X } from "lucide-react";

import { MarkerGlyph } from "@/components/MarkerGlyph";
import { Button } from "@/components/ui/button";
import { Portal } from "@/components/ui/Portal";
import { useDialogFocus } from "@/lib/dialogFocus";
import { cn } from "@/lib/utils";
import type { AuditMarker } from "@/components/MarkerLayer";

type SortKey = "time" | "kind" | "confidence" | "peakAmplitude" | "candidateNumber";
type SortDir = "asc" | "desc";

interface ListDrawerProps {
  open: boolean;
  onClose: () => void;
  markers: AuditMarker[];
  currentMarkerId: string | null;
  onJumpTo: (marker: AuditMarker) => void;
  onDelete?: (marker: AuditMarker) => void;
}

export function ListDrawer({
  open,
  onClose,
  markers,
  currentMarkerId,
  onJumpTo,
  onDelete,
}: ListDrawerProps) {
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const panelRef = useRef<HTMLDivElement | null>(null);

  // Escape + focus entry/restore. Deliberately NON-modal (trap: false):
  // the drawer is a companion surface -- clicking rows scrubs the
  // waveform behind it, so the page must stay interactive.
  useDialogFocus(open, panelRef, onClose, { trap: false });

  const sorted = [...markers].sort((a, b) => {
    const dir = sortDir === "asc" ? 1 : -1;
    const ax = pluck(a, sortKey);
    const bx = pluck(b, sortKey);
    if (ax == null && bx == null) return 0;
    if (ax == null) return 1;
    if (bx == null) return -1;
    if (typeof ax === "string" && typeof bx === "string") return ax.localeCompare(bx) * dir;
    return ((ax as number) - (bx as number)) * dir;
  });

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  return (
    <Portal>
    <div
      ref={panelRef}
      role="dialog"
      aria-label="Marker list"
      aria-hidden={!open}
      // inert while closed: aria-hidden alone leaves the off-screen rows
      // in the Tab order.
      inert={!open}
      className={cn(
        "fixed right-0 top-0 z-drawer flex h-full w-full max-w-md flex-col",
        "border-l border-rule bg-surface shadow-xl",
        "transition-transform duration-200 ease-out",
        open ? "translate-x-0" : "translate-x-full",
      )}
    >
      <div className="flex items-center justify-between border-b border-rule px-4 py-3">
        <h2 className="text-sm font-semibold">All markers ({markers.length})</h2>
        <Button
          size="sm"
          variant="ghost"
          onClick={onClose}
          aria-label="Close drawer (Esc or L)"
        >
          <X className="size-4" />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {markers.length === 0 ? (
          <p className="p-4 text-sm text-muted">
            No candidates yet. Run shot detection on this stage, or double-click
            the waveform to add manual markers.
          </p>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface text-muted">
              <tr className="border-b border-rule">
                <SortHeader
                  active={sortKey === "time"}
                  dir={sortDir}
                  onClick={() => toggleSort("time")}
                  className="text-left"
                >
                  Time
                </SortHeader>
                <SortHeader
                  active={sortKey === "kind"}
                  dir={sortDir}
                  onClick={() => toggleSort("kind")}
                  className="text-left"
                >
                  Kind
                </SortHeader>
                <SortHeader
                  active={sortKey === "confidence"}
                  dir={sortDir}
                  onClick={() => toggleSort("confidence")}
                  className="text-right"
                >
                  Conf
                </SortHeader>
                <SortHeader
                  active={sortKey === "peakAmplitude"}
                  dir={sortDir}
                  onClick={() => toggleSort("peakAmplitude")}
                  className="text-right"
                >
                  Peak
                </SortHeader>
                <SortHeader
                  active={sortKey === "candidateNumber"}
                  dir={sortDir}
                  onClick={() => toggleSort("candidateNumber")}
                  className="text-right"
                >
                  #
                </SortHeader>
                <th scope="col" className="w-8 px-1 py-2" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((m) => {
                const selected = m.id === currentMarkerId;
                return (
                  <tr
                    key={m.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`Jump to ${m.kind} marker at ${m.time.toFixed(3)}s`}
                    className={cn(
                      "cursor-pointer border-b border-rule/50 hover:bg-surface-3",
                      "focus-visible:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led",
                      selected && "bg-surface-3",
                    )}
                    onClick={() => onJumpTo(m)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onJumpTo(m);
                      }
                    }}
                  >
                    <td className="px-2 py-1 font-mono tabular-nums">
                      {m.time.toFixed(3)}s
                    </td>
                    <td className="px-2 py-1">
                      <span className="inline-flex items-center gap-1">
                        <MarkerGlyph kind={m.kind} size={11} />
                        <span className="capitalize">{m.kind}</span>
                      </span>
                    </td>
                    <td className="px-2 py-1 text-right font-mono tabular-nums">
                      {m.confidence != null ? m.confidence.toFixed(2) : "--"}
                    </td>
                    <td className="px-2 py-1 text-right font-mono tabular-nums">
                      {m.peakAmplitude != null ? m.peakAmplitude.toFixed(2) : "--"}
                    </td>
                    <td className="px-2 py-1 text-right font-mono tabular-nums">
                      {m.candidateNumber ?? "--"}
                    </td>
                    <td className="px-1 py-1 text-right">
                      {m.kind === "manual" && onDelete ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 w-6 p-0"
                          aria-label={`Delete manual marker at ${m.time.toFixed(3)}s`}
                          title="Delete manual marker"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDelete(m);
                          }}
                        >
                          <Trash2 className="size-3" />
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <div className="border-t border-rule px-4 py-2 text-[0.7rem] text-muted">
        Press L to toggle - Esc to close
      </div>
    </div>
    </Portal>
  );
}

function SortHeader({
  active,
  dir,
  onClick,
  className,
  children,
}: {
  active: boolean;
  dir: SortDir;
  onClick: () => void;
  className?: string;
  children: React.ReactNode;
}) {
  // Wrap the column label in a real <button> so screen readers announce
  // the sort affordance and keyboard nav can hit Enter to toggle order.
  return (
    <th scope="col" className={cn("px-2 py-2 font-medium", className)} aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "inline-flex cursor-pointer items-center gap-0.5",
          "rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
        )}
      >
        {children}
        {active ? (
          dir === "asc" ? (
            <ArrowUp className="size-3" aria-hidden />
          ) : (
            <ArrowDown className="size-3" aria-hidden />
          )
        ) : null}
      </button>
    </th>
  );
}

function pluck(m: AuditMarker, key: SortKey): number | string | null {
  switch (key) {
    case "time":
      return m.time;
    case "kind":
      return m.kind;
    case "confidence":
      return m.confidence;
    case "peakAmplitude":
      return m.peakAmplitude;
    case "candidateNumber":
      return m.candidateNumber;
  }
}
