/**
 * The handle between Breakdown's workspace and its timeline band (#1373).
 * A drag trades video height for band height; the arrow keys move it a
 * step (Shift a larger one), Home and End go to the limits, a double-click
 * toggles the band-large preset. Limits and the remembered split are the
 * page's (``useBandSplit``, ``lib/breakdown``); this file owns the gesture.
 *
 * ARIA's window splitter: the value is the viewer's height (the pane
 * before the separator), so Home is the viewer at its floor.
 */
import { useRef } from "react";

import { type BandLimits, bandForKey, clampBand } from "@/lib/breakdown";

export interface BandSplitterProps {
  /** The band's height on screen. */
  band: number;
  limits: BandLimits;
  /** The height the viewer row and the band share. */
  room: number;
  /** A drag frame: draw this band height, remember nothing yet. */
  onDrag: (band: number) => void;
  /** Remember this band height (a drag's release, a key). */
  onCommit: (band: number) => void;
  /** A drag the system took away: back to the remembered split. */
  onCancel: () => void;
  onToggleLarge: () => void;
}

export function BandSplitter({ band, limits, room, onDrag, onCommit, onCancel, onToggleLarge }: BandSplitterProps) {
  const drag = useRef<{ pointerId: number; y: number; band: number; last: number } | null>(null);
  const end = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.pointerId !== e.pointerId) return null;
    drag.current = null;
    if (e.currentTarget.hasPointerCapture?.(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    return d;
  };
  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label="Resize the timeline"
      aria-valuemin={Math.round(room - limits.max)}
      aria-valuemax={Math.round(room - limits.min)}
      aria-valuenow={Math.round(room - band)}
      tabIndex={0}
      data-testid="band-splitter"
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture?.(e.pointerId);
        drag.current = { pointerId: e.pointerId, y: e.clientY, band, last: band };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d || d.pointerId !== e.pointerId) return;
        // Up (a smaller clientY) is a taller band.
        d.last = clampBand(d.band - (e.clientY - d.y), limits);
        onDrag(d.last);
      }}
      onPointerUp={(e) => {
        const d = end(e);
        // A press without movement remembers nothing (a double-click's presses).
        if (d && d.last !== d.band) onCommit(d.last);
        else if (d) onCancel();
      }}
      onPointerCancel={(e) => {
        if (end(e)) onCancel();
      }}
      onDoubleClick={onToggleLarge}
      onKeyDown={(e) => {
        const next = bandForKey(e.key, e.shiftKey, band, limits);
        if (next === null) return;
        e.preventDefault();
        onCommit(next);
      }}
      className="group flex h-1.5 shrink-0 cursor-row-resize touch-none items-center justify-center border-y border-rule bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led"
    >
      <span aria-hidden className="h-0.5 w-10 rounded-full bg-rule-strong group-hover:bg-ink-2" />
    </div>
  );
}
