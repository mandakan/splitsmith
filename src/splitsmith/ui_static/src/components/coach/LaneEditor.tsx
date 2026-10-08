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
 * nudge commits.
 */
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { useEffect, useRef } from "react";

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
  type Drag,
  type DragInit,
} from "./laneDrag";

export { DRAG_THRESHOLD_PX, LANES, SNAP_PX, TOUCH_DRAG_THRESHOLD_PX } from "./laneDrag";

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
  /** Optional overflow-menu slot rendered at the strip's top right. */
  menu?: ReactNode;
}

export function LaneEditor(props: LaneEditorProps) {
  const { shots, events, stageTime, fps = 30, currentTime, selectedId, readOnly = false } = props;
  const { onSelect, onSeek, onChange, menu } = props;
  const rootRef = useRef<HTMLDivElement | null>(null);
  const stripRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<Drag | null>(null);
  // The list as last emitted: a drag frame reads it before the parent re-renders.
  const eventsRef = useRef(events);
  eventsRef.current = events;

  const duration = Math.max(stageTime, 0.001);
  const pct = (t: number) => `${(Math.min(Math.max(t, 0), duration) / duration) * 100}%`;
  const tAt = (clientX: number) => {
    const rect = stripRef.current?.getBoundingClientRect();
    return rect ? timeFromX(clientX - rect.left, rect.width, duration) : 0;
  };
  const maybeSnap = (t: number, alt: boolean) => {
    if (alt) return t;
    const width = Math.max(stripRef.current?.getBoundingClientRect().width ?? 0, 1);
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
    };
    if (init.mode !== "create") onSelect(init.id);
  };
  const startCreate = (e: ReactPointerEvent<HTMLElement>, kind: StageEventKind) =>
    begin(e, { mode: "create", kind, anchorT: maybeSnap(tAt(e.clientX), e.altKey), id: null });
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
  };

  const handleUp = (e: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (drag?.pointerId !== e.pointerId) return;
    release(drag);
    dragRef.current = null;
    if (!drag.moved) {
      // A click on empty lane space seeks there and clears the selection.
      if (drag.mode === "create") {
        onSelect(null);
        onSeek(drag.anchorT);
      }
      return;
    }
    if (drag.mode === "create" && drag.id === null) return;
    emit(eventsRef.current, true);
    if (drag.mode === "create") onSelect(drag.id);
  };

  const cancelDrag = () => {
    const drag = dragRef.current;
    if (!drag) return;
    release(drag);
    dragRef.current = null;
    const restored = cancelFrame(drag, eventsRef.current);
    if (restored) emit(restored, false);
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
    // Only keys aimed at the editor itself: the menu slot's keys bubble here too (React
    // bubbles through portals), and an arrow there must not nudge -- each nudge is a save.
    if (e.target !== e.currentTarget) return;
    if (readOnly || !selectedId || dragRef.current) return;
    const current = eventsRef.current;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const dir = e.key === "ArrowLeft" ? -1 : 1;
      const next = nudge(current, selectedId, dir, { alt: e.altKey, shift: e.shiftKey }, fps);
      if (next) emit(next, true);
    } else if ((e.key === "Delete" || e.key === "Backspace") && current.some((x) => x.id === selectedId)) {
      e.preventDefault();
      emit(
        current.filter((x) => x.id !== selectedId),
        true,
      );
      onSelect(null);
    }
  };

  const rulerLabels: number[] = [];
  for (let s = 2; s < duration - 0.9; s += 2) rulerLabels.push(s);
  const ticks: number[] = [];
  for (let s = 1; s < duration; s += 1) ticks.push(s);
  const selected = events.find((x) => x.id === selectedId);
  const overhangOf = selected?.kind === "reload" ? enclosingMovement(selected, events) : null;

  return (
    <div
      ref={rootRef}
      data-testid="lane-editor"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className="rounded-[10px] border border-rule bg-surface-2 outline-none focus-visible:ring-2 focus-visible:ring-led"
    >
      <div className="flex items-center justify-between px-3 pt-2">
        <Label>Lanes</Label>
        {menu}
      </div>
      <div className="grid grid-cols-[5.5rem_1fr] px-3 py-2">
        <div className="flex flex-col">
          <div className="h-5" />
          <div className="flex h-8 items-center">
            <Label>Shots</Label>
          </div>
          {LANES.map((kind) => (
            <div key={kind} className="flex h-9 items-center">
              <Label>{LANE_LABEL[kind]}</Label>
            </div>
          ))}
        </div>
        <div ref={stripRef} className="relative">
          <div
            data-testid="lane-ruler"
            className="relative h-5 cursor-pointer border-b border-rule"
            onClick={(e) => onSeek(tAt(e.clientX))}
          >
            {ticks.map((s) => (
              <span key={s} className="absolute bottom-0 h-1 w-px bg-rule" style={{ left: pct(s) }} />
            ))}
            <Label className="absolute left-0 top-0 text-beep">Beep</Label>
            {rulerLabels.map((s) => (
              <span key={s} className="numeral absolute top-0 -translate-x-1/2 text-xs text-muted" style={{ left: pct(s) }}>
                {s}
              </span>
            ))}
            <span className="numeral absolute right-0 top-0 text-xs text-muted">{stageTime.toFixed(2)}</span>
          </div>
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
                  {moving && (
                    <span className="absolute -top-1 left-1/2 size-[5px] -translate-x-1/2 rounded-full border border-beep" />
                  )}
                </span>
              );
            })}
          </div>
          {LANES.map((kind) => (
            <div
              key={kind}
              data-testid={`lane-${kind}`}
              className={cn("relative h-9 border-t border-rule/60", !readOnly && "cursor-crosshair")}
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
              {kind === "reload" && selected && overhangOf && (
                <OverhangBracket from={overhangOf.end} to={selected.end} pct={pct} />
              )}
            </div>
          ))}
          <div
            data-testid="playhead"
            className="pointer-events-none absolute inset-y-0 w-px bg-led"
            style={{ left: pct(currentTime) }}
          />
        </div>
      </div>
      {!readOnly && (
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
      )}
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
