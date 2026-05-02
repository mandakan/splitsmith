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

import { useCallback, useEffect, useRef } from "react";

import { MarkerGlyph, type MarkerKind } from "@/components/MarkerGlyph";
import { cn } from "@/lib/utils";

/** Detector resolution -- coarse hop length in seconds for the shot detector
 *  pipeline. Drag-snap rounds to multiples of this so users can't land on
 *  positions finer than the detector itself can resolve. Hold Shift to bypass
 *  and land on the 1 ms grid. */
export const DETECTOR_RESOLUTION_S = 0.0107;
const FINE_NUDGE_S = 0.001;

/** Pointer travel (in CSS pixels) before a press is treated as a drag rather
 *  than a click. Trackpad clicks routinely register a few stray pixels of
 *  motion; without this threshold those clicks accidentally moved the marker.
 *  6px matches the de-facto threshold in FCP / Logic / most native UIs. */
const DRAG_THRESHOLD_PX = 6;

/** Idle window after the last keyboard nudge before the burst is committed
 *  as a single undo entry. 350 ms is long enough to chord arrow taps without
 *  splitting them, short enough that the Cmd+Z right after feels responsive. */
const NUDGE_COMMIT_IDLE_MS = 350;

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
  /** Live time update during a drag or nudge. Parent should mutate state
   *  for visual feedback but NOT push to its undo stack -- intermediate
   *  positions are noise. Use ``onTimeChangeBegin`` / ``onTimeChangeCommit``
   *  to bracket the gesture and snapshot the from/to once. */
  onTimeChange: (id: string, time: number) => void;
  /** Called once at the start of a drag (or first keyboard nudge in a
   *  burst). Parent records the marker's pre-edit time so the
   *  matching commit can produce a single from->to undo entry. */
  onTimeChangeBegin?: (id: string) => void;
  /** Called once at the end of a drag (pointerup / pointercancel) or at
   *  the end of a keyboard nudge burst. Parent pushes the bracketed
   *  edit to its undo stack and audit_events log. */
  onTimeChangeCommit?: (id: string, time: number) => void;
  /** Categories to render. Hiding ``rejected`` while saving keeps the
   *  rejected markers in the model -- they just don't render. The save
   *  flow still serializes them via the audit JSON. */
  visibleKinds?: Set<MarkerKind>;
}

export function MarkerLayer({
  markers,
  duration,
  focusedId,
  onFocusChange,
  onClick,
  onDelete,
  onTimeChange,
  onTimeChangeBegin,
  onTimeChangeCommit,
  visibleKinds,
}: MarkerLayerProps) {
  if (duration <= 0) return null;

  // Drag state lives in a ref so re-renders driven by external time
  // updates don't reset the active drag. ``moved`` only flips once the
  // cursor crosses DRAG_THRESHOLD_PX -- before that, the gesture is
  // still ambiguous and pointerup will fire as a click.
  const dragRef = useRef<{
    pointerId: number;
    element: HTMLButtonElement;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);

  // Keyboard-nudge burst tracking: the first arrow key in a burst calls
  // onTimeChangeBegin; subsequent keys within NUDGE_COMMIT_IDLE_MS just
  // update live. The trailing-edge timer commits one bracketed entry.
  const nudgeRef = useRef<{
    id: string;
    lastTime: number;
    timer: number;
  } | null>(null);

  const flushNudge = useCallback(() => {
    const n = nudgeRef.current;
    if (!n) return;
    window.clearTimeout(n.timer);
    nudgeRef.current = null;
    onTimeChangeCommit?.(n.id, n.lastTime);
  }, [onTimeChangeCommit]);

  // If the focused marker changes mid-burst, commit before the new marker
  // starts its own burst so the two don't merge into one undo entry.
  useEffect(() => {
    const n = nudgeRef.current;
    if (n && n.id !== focusedId) flushNudge();
  }, [focusedId, flushNudge]);

  // Final flush on unmount (stage switch, etc.) so a half-typed burst
  // doesn't disappear without producing an audit event.
  useEffect(() => {
    return () => flushNudge();
  }, [flushNudge]);

  // Markers in time order so Tab moves left-to-right naturally. Filtered
  // categories are dropped entirely so they don't intercept pointer events
  // or appear in keyboard tab order.
  const sorted = markers
    .slice()
    .filter((m) => (visibleKinds ? visibleKinds.has(m.kind) : true))
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
      dragRef.current = {
        pointerId: e.pointerId,
        element: el,
        startX: e.clientX,
        startY: e.clientY,
        moved: false,
      };
      onFocusChange(marker.id);
    },
    [onFocusChange],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>, marker: AuditMarker) => {
      const drag = dragRef.current;
      if (drag?.pointerId !== e.pointerId) return;
      if (!drag.moved) {
        const dx = e.clientX - drag.startX;
        const dy = e.clientY - drag.startY;
        if (dx * dx + dy * dy < DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX) return;
        drag.moved = true;
        // Drag committed past the threshold: tell the parent to snapshot
        // the pre-edit time. Subsequent onTimeChange calls are visual
        // only; the matching commit on pointerup brackets the gesture.
        onTimeChangeBegin?.(marker.id);
      }
      const parent = e.currentTarget.parentElement;
      if (!parent) return;
      const rect = parent.getBoundingClientRect();
      const t = snap(timeFromClientX(e.clientX, rect), e.shiftKey);
      onTimeChange(marker.id, t);
    },
    [snap, timeFromClientX, onTimeChange, onTimeChangeBegin],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>, marker: AuditMarker) => {
      const drag = dragRef.current;
      if (drag?.pointerId !== e.pointerId) return;
      const el = e.currentTarget;
      if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
      const moved = drag.moved;
      dragRef.current = null;
      if (!moved) {
        onClick(marker);
        return;
      }
      // Commit the drag: parent reads the marker's current time from its
      // own state (it's been updating live during pointermove) and pushes
      // a single from->to undo entry plus one audit_events row.
      const live = markers.find((m) => m.id === marker.id);
      onTimeChangeCommit?.(marker.id, live?.time ?? marker.time);
    },
    [onClick, onTimeChangeCommit, markers],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>, marker: AuditMarker) => {
      switch (e.key) {
        case "ArrowLeft":
        case "ArrowRight": {
          e.preventDefault();
          const dir = e.key === "ArrowRight" ? 1 : -1;
          const step = e.shiftKey ? FINE_NUDGE_S : DETECTOR_RESOLUTION_S;
          const next = snap(
            Math.max(0, Math.min(duration, marker.time + dir * step)),
            e.shiftKey,
          );
          // First key in a burst -> open the bracket. Subsequent keys
          // just refresh the trailing-edge timer.
          if (nudgeRef.current?.id !== marker.id) {
            flushNudge();
            onTimeChangeBegin?.(marker.id);
            nudgeRef.current = {
              id: marker.id,
              lastTime: next,
              timer: window.setTimeout(flushNudge, NUDGE_COMMIT_IDLE_MS),
            };
          } else {
            window.clearTimeout(nudgeRef.current.timer);
            nudgeRef.current.lastTime = next;
            nudgeRef.current.timer = window.setTimeout(
              flushNudge,
              NUDGE_COMMIT_IDLE_MS,
            );
          }
          onTimeChange(marker.id, next);
          return;
        }
        case "Enter":
        case " ":
          e.preventDefault();
          flushNudge();
          onClick(marker);
          return;
        case "Delete":
        case "Backspace":
          e.preventDefault();
          flushNudge();
          onDelete(marker);
          return;
        default:
          return;
      }
    },
    [duration, onClick, onDelete, onTimeChange, onTimeChangeBegin, snap, flushNudge],
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
              <MarkerGlyph kind={m.kind} size={18} />
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
