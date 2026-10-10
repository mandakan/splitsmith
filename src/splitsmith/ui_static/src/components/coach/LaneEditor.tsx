/**
 * Lanes under the Coach video: shots (read-only) plus movement, reload and
 * activation regions, edited by pointer (spec 2026-10-08). Pointer rules
 * follow MarkerLayer: pointer capture, a travel threshold before a press
 * becomes a drag (wider for touch), Esc restores the pre-drag state. While
 * an edge drags the video seeks to it -- the frame under the cursor is what
 * is being marked. Geometry lives in lib/events.ts and the per-gesture frame
 * math in ./laneDrag.ts; this file owns the DOM.
 *
 * Every ``onChange(events, true)`` is a server write upstream, so drag
 * frames go out with ``commit=false`` and only a release or a keyboard
 * nudge commits. A release or nudge that lands back on the pre-gesture list
 * (by value, ``laneDrag.sameEvents``) commits nothing: a release still ends
 * the live gesture through ``onCancel``, same as Esc or pointercancel (#1325).
 *
 * The editor is a track inside the shared Timeline band (spec
 * 2026-10-09): the band owns the ruler, the playhead and the gutter
 * labels (``LANE_ROWS``), so this root is the strip itself, positioned as
 * a percentage of the band's zoomed content width exactly as it was a
 * percentage of its own measured width before.
 */
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { useEffect, useRef, useState } from "react";

import { Kbd } from "@/components/ui/Kbd";
import { Label } from "@/components/ui/Label";
import type { StageEvent, StageEventKind } from "@/lib/api";
import { enclosingMovement, shotIsMoving, snapTime, timeFromX } from "@/lib/events";
import { cn } from "@/lib/utils";

import {
  DRAG_THRESHOLD_PX,
  LANES,
  SNAP_PX,
  TOUCH_DRAG_THRESHOLD_PX,
  cancelFrame,
  dragFrame,
  nudge,
  sameEvents,
  type Drag,
  type DragInit,
} from "./laneDrag";

export { DRAG_THRESHOLD_PX, LANES, SNAP_PX, TOUCH_DRAG_THRESHOLD_PX } from "./laneDrag";

/** Gutter rows for the Timeline band: Shots, then one per lane, current heights (h-8 / h-9). */
export const LANE_ROWS: { label: string; height: number }[] = [
  { label: "Shots", height: 32 },
  { label: "Movement", height: 36 },
  { label: "Reload", height: 36 },
  { label: "Activation", height: 36 },
];

const LANE_LABEL: Record<StageEventKind, string> = { movement: "Movement", reload: "Reload", activation: "Activation" };
// Budget hues (Chip TICK): movement beep, reload live, activation ink-2.
const LANE_FILL: Record<StageEventKind, string> = {
  movement: "bg-beep/30 border-beep",
  reload: "bg-live/30 border-live",
  activation: "bg-ink-2/20 border-ink-2",
};
const HANDLE_FILL: Record<StageEventKind, string> = { movement: "bg-beep", reload: "bg-live", activation: "bg-ink-2" };

export interface LaneEditorProps {
  shots: { shot_number: number; time_from_beep: number }[];
  events: StageEvent[];
  /** Right edge of the strip, seconds from beep (stage time, or last shot + 1 when unknown). */
  stageTime: number;
  /** Frames per second for nudges; 30 when unknown. */
  fps?: number;
  /** Playhead, seconds from beep. */
  currentTime: number;
  selectedId: string | null;
  readOnly?: boolean;
  onSelect: (id: string | null) => void;
  onSeek: (tFromBeep: number) => void;
  /** Live during a drag (commit=false), once on release / per nudge (commit=true). */
  onChange: (events: StageEvent[], commit: boolean) => void;
  /**
   * A drag ended without a commit (Esc, pointercancel, or a release that
   * landed back on the pre-gesture list, #1325), called after any restored
   * list goes out through ``onChange(..., false)``: the live gesture is
   * over and nothing will commit it.
   */
  onCancel?: () => void;
}

export function LaneEditor(props: LaneEditorProps) {
  const { shots, events, stageTime, fps = 30, selectedId, readOnly = false } = props;
  const { onSelect, onSeek, onChange, onCancel } = props;
  const rootRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<Drag | null>(null);
  // The list as last emitted: a drag frame reads it before the parent re-renders.
  const eventsRef = useRef(events);
  eventsRef.current = events;
  // The time pill: where the drag's moving edge (the seek target) stands, null between drags.
  const [pill, setPill] = useState<number | null>(null);

  const duration = Math.max(stageTime, 0.001);
  const pct = (t: number) => `${(Math.min(Math.max(t, 0), duration) / duration) * 100}%`;
  const tAt = (clientX: number) => {
    const rect = rootRef.current?.getBoundingClientRect();
    return rect ? timeFromX(clientX - rect.left, rect.width, duration) : 0;
  };
  const maybeSnap = (t: number, alt: boolean) => {
    if (alt) return t;
    const width = Math.max(rootRef.current?.getBoundingClientRect().width ?? 0, 1);
    return snapTime(t, [0, ...shots.map((s) => s.time_from_beep)], (SNAP_PX / width) * duration);
  };
  const emit = (next: StageEvent[], commit: boolean) => {
    eventsRef.current = next;
    onChange(next, commit);
  };

  const begin = (e: ReactPointerEvent<HTMLElement>, init: DragInit) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture?.(e.pointerId);
    rootRef.current?.focus({ preventScroll: true });
    dragRef.current = {
      ...init,
      pointerId: e.pointerId,
      element: el,
      startX: e.clientX,
      startY: e.clientY,
      thresholdPx: e.pointerType === "touch" ? TOUCH_DRAG_THRESHOLD_PX : DRAG_THRESHOLD_PX,
      moved: false,
      startEvents: eventsRef.current,
    };
    if (init.mode !== "create") onSelect(init.id);
  };
  const startCreate = (e: ReactPointerEvent<HTMLElement>, kind: StageEventKind) => {
    // A shot just inside a neighbour's edge must not pull the anchor into
    // that neighbour: the create's clamp only sees regions on either side.
    const raw = tAt(e.clientX);
    const snapped = maybeSnap(raw, e.altKey);
    const inside = eventsRef.current.some((x) => x.kind === kind && x.start < snapped && snapped < x.end);
    begin(e, { mode: "create", kind, anchorT: inside ? raw : snapped, id: null });
  };
  const startEdge = (e: ReactPointerEvent<HTMLElement>, id: string, edge: "start" | "end") => {
    const before = eventsRef.current.find((x) => x.id === id);
    if (before) begin(e, { mode: "edge", edge, id, before });
  };
  const startBody = (e: ReactPointerEvent<HTMLElement>, id: string) => {
    const before = eventsRef.current.find((x) => x.id === id);
    if (before) begin(e, { mode: "body", id, before, grabOffsetT: tAt(e.clientX) - before.start });
  };

  const release = (drag: Drag) => {
    if (drag.element.hasPointerCapture?.(drag.pointerId)) drag.element.releasePointerCapture(drag.pointerId);
  };

  // Move and up are bound on the lane: events from a captured handle or body bubble to it.
  const handleMove = (e: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (drag?.pointerId !== e.pointerId) return;
    if (!drag.moved) {
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (dx * dx + dy * dy < drag.thresholdPx * drag.thresholdPx) return;
      drag.moved = true;
    }
    const frame = dragFrame(drag, eventsRef.current, tAt(e.clientX), (t) => maybeSnap(t, e.altKey), duration);
    if (!frame) return;
    if (drag.mode === "create" && frame.id) drag.id = frame.id;
    emit(frame.events, false);
    onSeek(frame.seek);
    setPill(frame.seek);
  };

  const handleUp = (e: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (drag?.pointerId !== e.pointerId) return;
    release(drag);
    dragRef.current = null;
    setPill(null);
    if (!drag.moved) {
      // A click on empty lane space seeks there and clears the selection.
      if (drag.mode === "create") {
        onSelect(null);
        onSeek(drag.anchorT);
      }
      return;
    }
    if (drag.mode === "create" && drag.id === null) return;
    if (sameEvents(eventsRef.current, drag.startEvents)) {
      // Travelled past the threshold but landed back on the pre-gesture list
      // (a body or edge drag that ends where it started): no commit. A
      // no-op never confirms a proposal, so an ``auto`` region's mid-drag
      // ``touched()`` flip to ``manual`` is undone too -- restored exactly
      // as Esc would -- and the live gesture ends the same way (#1325).
      const restored = cancelFrame(drag, eventsRef.current);
      if (restored) emit(restored, false);
      onCancel?.();
      return;
    }
    emit(eventsRef.current, true);
    if (drag.mode === "create") onSelect(drag.id);
  };

  const cancelDrag = () => {
    const drag = dragRef.current;
    if (!drag) return;
    release(drag);
    dragRef.current = null;
    setPill(null);
    const restored = cancelFrame(drag, eventsRef.current);
    if (restored) emit(restored, false);
    onCancel?.();
  };
  const cancelRef = useRef(cancelDrag);
  cancelRef.current = cancelDrag;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && dragRef.current) {
        e.preventDefault();
        cancelRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const handleKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    // Only keys aimed at the root itself: a focusable descendant (a region, a
    // handle, a future control inside the strip) bubbles its keydown here too,
    // and an arrow or Delete there must not nudge or delete the selection --
    // each nudge is a save, so only the root's own keys drive it.
    if (e.target !== e.currentTarget) return;
    if (readOnly || !selectedId || dragRef.current) return;
    const current = eventsRef.current;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const dir = e.key === "ArrowLeft" ? -1 : 1;
      const next = nudge(current, selectedId, dir, { alt: e.altKey, shift: e.shiftKey }, fps);
      // A clamp (a neighbour, or the 0 floor / stage-time ceiling) can leave the
      // region exactly where it was: that nudge commits nothing (#1325).
      if (next && !sameEvents(next, current)) emit(next, true);
    } else if ((e.key === "Delete" || e.key === "Backspace") && current.some((x) => x.id === selectedId)) {
      e.preventDefault();
      emit(
        current.filter((x) => x.id !== selectedId),
        true,
      );
      onSelect(null);
    }
  };

  const selected = events.find((x) => x.id === selectedId);
  const overhangOf = selected?.kind === "reload" ? enclosingMovement(selected, events) : null;

  return (
    <div
      ref={rootRef}
      data-testid="lane-editor"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className="relative outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led"
    >
      <div className="relative h-8">
        {shots.map((shot) => {
          const moving = shotIsMoving(shot.time_from_beep, events);
          return (
            <span
              key={shot.shot_number}
              data-testid={`shot-${shot.shot_number}`}
              data-moving={String(moving)}
              className="absolute bottom-1.5 top-1.5 w-px bg-done"
              style={{ left: pct(shot.time_from_beep) }}
            >
              {moving && <span className="absolute -top-1 left-1/2 size-[5px] -translate-x-1/2 rounded-full border border-beep" />}
            </span>
          );
        })}
      </div>
      {LANES.map((kind) => (
        <div
          key={kind}
          data-testid={`lane-${kind}`}
          // touch-none: a touch drag on an editable lane is an edit, never a pan. Without it the
          // browser takes the gesture over mid-drag and fires pointercancel, which undoes the drag.
          className={cn("relative h-9 border-t border-rule/60", !readOnly && "cursor-crosshair touch-none")}
          onPointerDown={readOnly ? undefined : (e) => startCreate(e, kind)}
          onPointerMove={readOnly ? undefined : handleMove}
          onPointerUp={readOnly ? undefined : handleUp}
          onPointerCancel={readOnly ? undefined : () => cancelDrag()}
        >
          {events
            .filter((x) => x.kind === kind)
            .map((x) => (
              <div
                key={x.id}
                role="option"
                aria-selected={selectedId === x.id}
                aria-label={LANE_LABEL[kind]}
                data-testid={`event-${x.id}`}
                data-source={x.source}
                data-start={x.start}
                data-end={x.end}
                onClick={() => onSelect(x.id)}
                onPointerDown={readOnly ? undefined : (e) => startBody(e, x.id)}
                className={cn(
                  "absolute bottom-1.5 top-1.5 rounded border",
                  LANE_FILL[kind],
                  x.source === "auto" && "border-dashed bg-transparent",
                  selectedId === x.id && "ring-1 ring-ink",
                  !readOnly && "cursor-grab",
                )}
                style={{ left: pct(x.start), width: pct(x.end - x.start) }}
              >
                {x.source === "auto" && (
                  <Label
                    tone="ink"
                    data-testid={`auto-${x.id}`}
                    // Runs past a short region rather than truncating to "A...".
                    className="pointer-events-none absolute left-2 top-1/2 block -translate-y-1/2 whitespace-nowrap"
                  >
                    Auto ?
                  </Label>
                )}
                {!readOnly &&
                  (["start", "end"] as const).map((edge) => (
                    <span
                      key={edge}
                      data-testid={`handle-${x.id}-${edge}`}
                      onPointerDown={(e) => startEdge(e, x.id, edge)}
                      className={cn(
                        "absolute top-1/2 h-4 w-2 -translate-y-1/2 cursor-ew-resize rounded-sm",
                        edge === "start" ? "-left-1" : "-right-1",
                        HANDLE_FILL[kind],
                      )}
                    />
                  ))}
              </div>
            ))}
          {kind === "reload" && selected && overhangOf && <OverhangBracket from={overhangOf.end} to={selected.end} pct={pct} />}
        </div>
      ))}
      {pill !== null && (
        <span
          data-testid="drag-pill"
          className="numeral pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded border border-rule-strong bg-surface px-1 text-xs text-ink"
          style={{ left: pct(pill) }}
        >
          {pill.toFixed(2)} s <span className="text-muted">f {Math.round(pill * fps)}</span>
        </span>
      )}
    </div>
  );
}

/** The lane editor's control hints, drawn by the page under the Timeline band. */
export function LaneHints({ readOnly }: { readOnly?: boolean }) {
  if (readOnly) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 pb-2 text-xs text-muted">
      <span>Drag empty lane to add</span>
      <span>Drag edge to resize, body to move</span>
      <span>
        <Kbd>Arrows</Kbd> nudge a frame, <Kbd>Shift</Kbd> end, <Kbd>Alt</Kbd> 100 ms
      </span>
      <span>
        <Kbd>Alt</Kbd>-drag skips snap, <Kbd>Esc</Kbd> cancels, <Kbd>Del</Kbd> removes
      </span>
    </div>
  );
}

/** Reload end minus the enclosing movement's end, drawn between the two. */
function OverhangBracket({ from, to, pct }: { from: number; to: number; pct: (t: number) => string }) {
  const lo = Math.min(from, to);
  const d = to - from;
  return (
    <div
      className="pointer-events-none absolute bottom-0 border-t border-dashed border-ink-2"
      style={{ left: pct(lo), width: pct(Math.abs(d)) }}
    >
      <span className="numeral absolute -top-4 left-1/2 -translate-x-1/2 text-xs text-ink-2">
        {d >= 0 ? "+" : "-"}
        {Math.abs(d).toFixed(2)}
      </span>
    </div>
  );
}
