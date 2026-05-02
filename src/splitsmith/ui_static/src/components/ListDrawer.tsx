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

import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, X } from "lucide-react";

import { MarkerGlyph } from "@/components/MarkerGlyph";
import { Button } from "@/components/ui/button";
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
}

export function ListDrawer({
  open,
  onClose,
  markers,
  currentMarkerId,
  onJumpTo,
}: ListDrawerProps) {
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // Close on Escape -- a global L toggle still works via the parent.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

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
    <div
      role="dialog"
      aria-label="Marker list"
      aria-hidden={!open}
      className={cn(
        "fixed right-0 top-0 z-40 flex h-full w-full max-w-md flex-col",
        "border-l border-border bg-card shadow-xl",
        "transition-transform duration-200 ease-out",
        open ? "translate-x-0" : "translate-x-full",
      )}
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
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
          <p className="p-4 text-sm text-muted-foreground">
            No candidates yet. Run shot detection on this stage, or double-click
            the waveform to add manual markers.
          </p>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-card text-muted-foreground">
              <tr className="border-b border-border">
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
              </tr>
            </thead>
            <tbody>
              {sorted.map((m) => {
                const selected = m.id === currentMarkerId;
                return (
                  <tr
                    key={m.id}
                    className={cn(
                      "cursor-pointer border-b border-border/50 hover:bg-accent",
                      selected && "bg-accent",
                    )}
                    onClick={() => onJumpTo(m)}
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
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <div className="border-t border-border px-4 py-2 text-[0.7rem] text-muted-foreground">
        Press L to toggle - Esc to close
      </div>
    </div>
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
  return (
    <th
      scope="col"
      className={cn("cursor-pointer px-2 py-2 font-medium", className)}
      onClick={onClick}
    >
      <span className="inline-flex items-center gap-0.5">
        {children}
        {active ? (
          dir === "asc" ? (
            <ArrowUp className="size-3" aria-hidden />
          ) : (
            <ArrowDown className="size-3" aria-hidden />
          )
        ) : null}
      </span>
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
