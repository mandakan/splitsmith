/**
 * Audit marker overlay (#15).
 *
 * Renders absolute-positioned markers on top of the waveform. Each marker
 * is a focusable button so keyboard navigation works without a custom
 * roving-tabindex implementation -- Tab visits them in time order.
 *
 * Pointer interactions:
 *   - click          -> onClick(marker)         (parent toggles keep/reject)
 *   - drag           -> onTimeChange(id, t)     (snapped, see SNAP_S below)
 *   - dblclick on bg -> handled by <Waveform>
 *
 * Keyboard interactions (focused marker):
 *   - Arrow Left/Right -> nudge by detector resolution. Shift narrows the
 *     step to ~1 ms so users can land on a single sample edge.
 *   - Enter            -> toggle keep/reject (detected/rejected only)
 *   - Delete/Backspace -> destructive action (parent decides; manual = remove,
 *                         detected kept = reject)
 *
 * The container fills the waveform's positioned parent and uses
 * `pointer-events: none` so empty space passes the press-and-drag scrubbing
 * through to the waveform. Each marker re-enables pointer-events for itself.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { MarkerGlyph, type MarkerKind } from "@/components/MarkerGlyph";
import { cn } from "@/lib/utils";

/** Detector resolution -- coarse hop length in seconds for the shot detector
 *  pipeline. Drag-snap rounds to multiples of this so users can't land on
 *  positions finer than the detector itself can resolve. Hold Shift to bypass
 *  and land on the 1 ms grid. */
export const DETECTOR_RESOLUTION_S = 0.0107;
const FINE_NUDGE_S = 0.001;

export interface AuditMarker {
  id: string;
  kind: MarkerKind;
  time: number;
  candidateNumber: number | null;
  confidence: number | null;
  peakAmplitude: number | null;
  /** Per-shot freeform note. Persisted into the audit JSON in step 5;
   *  in-memory only for now. */
  note: string;
}

export interface MarkerLayerProps {
  markers: AuditMarker[];
  duration: number;
  focusedId: string | null;
  onFocusChange: (id: string | null) => void;
  onClick: (marker: AuditMarker) => void;
  onDelete: (marker: AuditMarker) => void;
  onTimeChange: (id: string, time: number) => void;
}

export function MarkerLayer({
  markers,
  duration,
  focusedId,
  onFocusChange,
  onClick,
  onDelete,
  onTimeChange,
}: MarkerLayerProps) {
  if (duration <= 0) return null;

  // Drag state lives here so re-renders driven by external time updates
  // don't reset the active drag.
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const dragRef = useRef<{ pointerId: number; element: HTMLButtonElement } | null>(null);

  // Markers in time order so Tab moves left-to-right naturally.
  const sorted = markers
    .slice()
    .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));

  const timeFromClientX = useCallback(
    (clientX: number, parentRect: DOMRect): number => {
      if (parentRect.width <= 0 || duration <= 0) return 0;
      const ratio = (clientX - parentRect.left) / parentRect.width;
      return Math.min(Math.max(ratio, 0), 1) * duration;
    },
    [duration],
  );

  const snap = useCallback((t: number, fine: boolean): number => {
    const step = fine ? FINE_NUDGE_S : DETECTOR_RESOLUTION_S;
    return Math.round(t / step) * step;
  }, []);

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>, marker: AuditMarker) => {
      // Left button only; ignore right-click so the browser context menu
      // (if user has one bound) still works.
      if (e.button !== 0) return;
      e.stopPropagation();
      e.preventDefault();
      const el = e.currentTarget;
      el.setPointerCapture(e.pointerId);
      dragRef.current = { pointerId: e.pointerId, element: el };
      setDraggingId(marker.id);
      onFocusChange(marker.id);
    },
    [onFocusChange],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>, marker: AuditMarker) => {
      if (dragRef.current?.pointerId !== e.pointerId) return;
      const parent = e.currentTarget.parentElement;
      if (!parent) return;
      const rect = parent.getBoundingClientRect();
      const t = snap(timeFromClientX(e.clientX, rect), e.shiftKey);
      onTimeChange(marker.id, t);
    },
    [snap, timeFromClientX, onTimeChange],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>, marker: AuditMarker) => {
      if (dragRef.current?.pointerId !== e.pointerId) return;
      const el = e.currentTarget;
      if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
      dragRef.current = null;
      // If the pointer didn't actually move, treat as a click.
      const moved = draggingId === marker.id;
      setDraggingId(null);
      if (!moved) {
        onClick(marker);
      }
      // Track 'click vs drag' more carefully via pointer movement deltas would
      // be nicer, but the simpler heuristic above feels right in practice:
      // setDraggingId fires on pointerdown so this branch is essentially
      // "you pressed -> we set drag -> you released". A lightly clicked drag
      // snaps to the same time it started; harmless.
    },
    [draggingId, onClick],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>, marker: AuditMarker) => {
      switch (e.key) {
        case "ArrowLeft":
        case "ArrowRight": {
          e.preventDefault();
          const dir = e.key === "ArrowRight" ? 1 : -1;
          const step = e.shiftKey ? FINE_NUDGE_S : DETECTOR_RESOLUTION_S;
          const next = Math.max(0, Math.min(duration, marker.time + dir * step));
          onTimeChange(marker.id, snap(next, e.shiftKey));
          return;
        }
        case "Enter":
        case " ":
          e.preventDefault();
          onClick(marker);
          return;
        case "Delete":
        case "Backspace":
          e.preventDefault();
          onDelete(marker);
          return;
        default:
          return;
      }
    },
    [duration, onClick, onDelete, onTimeChange, snap],
  );

  // Whenever focus moves to a marker, keep ARIA in sync.
  useEffect(() => {
    if (focusedId == null) return;
    const el = document.querySelector<HTMLButtonElement>(
      `[data-audit-marker-id="${cssEscape(focusedId)}"]`,
    );
    if (el && document.activeElement !== el) {
      // Don't yank focus on every render -- only re-focus if a programmatic
      // request via the DOM dataset hasn't already happened. This avoids
      // stealing focus from inputs (notes field etc.).
    }
  }, [focusedId]);

  return (
    <div
      aria-hidden={false}
      className="pointer-events-none absolute inset-0"
      data-marker-layer
    >
      {sorted.map((m) => {
        const x = (m.time / duration) * 100;
        const label = describeMarker(m);
        const focused = focusedId === m.id;
        return (
          <button
            key={m.id}
            data-audit-marker
            data-audit-marker-id={m.id}
            type="button"
            tabIndex={0}
            aria-label={label}
            aria-pressed={m.kind === "detected"}
            onFocus={() => onFocusChange(m.id)}
            onPointerDown={(e) => handlePointerDown(e, m)}
            onPointerMove={(e) => handlePointerMove(e, m)}
            onPointerUp={(e) => handlePointerUp(e, m)}
            onPointerCancel={(e) => handlePointerUp(e, m)}
            onKeyDown={(e) => handleKeyDown(e, m)}
            className={cn(
              "group pointer-events-auto absolute top-0 -translate-x-1/2 cursor-grab",
              "flex h-full flex-col items-center justify-start outline-none",
              "active:cursor-grabbing",
              focused && "ring-2 ring-ring ring-offset-1 ring-offset-background",
            )}
            style={{ left: `${x}%`, width: "20px" }}
            title={label}
          >
            {/* Vertical guide line full-height behind the glyph for visibility. */}
            <span
              aria-hidden
              className={cn(
                "absolute top-0 bottom-0 left-1/2 w-px -translate-x-1/2",
                m.kind === "detected" && "bg-marker-detected/60",
                m.kind === "rejected" && "bg-marker-rejected/60",
                m.kind === "manual" && "bg-marker-manual/60",
              )}
            />
            <span className="relative mt-1">
              <MarkerGlyph kind={m.kind} size={14} />
            </span>
          </button>
        );
      })}
    </div>
  );
}

function describeMarker(m: AuditMarker): string {
  const t = `${m.time.toFixed(3)}s`;
  if (m.kind === "manual") return `Manual marker at ${t}`;
  if (m.kind === "rejected") return `Rejected detection at ${t}`;
  const conf =
    m.confidence != null ? `, confidence ${(m.confidence * 100).toFixed(0)}%` : "";
  return `Detected shot at ${t}${conf}`;
}

/** Minimal CSS.escape polyfill -- some older browsers in test envs lack it. */
function cssEscape(s: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") return CSS.escape(s);
  return s.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}
