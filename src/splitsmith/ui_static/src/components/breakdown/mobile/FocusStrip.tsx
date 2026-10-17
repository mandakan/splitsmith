/**
 * The phone's focus strip (spec 2026-10-17): one region and 2 s around it
 * (``lib/focusWindow``), with the ruler, the shots, the region's same-lane
 * neighbours and the playhead. Its handles are 44 px: a tap opens edge mode,
 * and a drag moves that edge under the finger on the strip's own scale (the
 * pad in edge mode is the fine control). It takes any region, a proposal or
 * a confirmed one.
 */
import { useRef } from "react";

import { DRAG_THRESHOLD_PX, LANE_FILL, TOUCH_DRAG_THRESHOLD_PX } from "@/components/coach/laneDrag";
import type { StageEvent } from "@/lib/api";
import { fracOf, type FocusWindow } from "@/lib/focusWindow";
import { cn } from "@/lib/utils";

const pct = (f: number) => `${f * 100}%`;

/** A handle dragged on the strip (the natural gesture; a tap opens edge mode too). */
export interface HandleDrag {
  start: (edge: "start" | "end") => void;
  /** The finger's travel since it went down, over the strip's width. */
  move: (dxFrac: number, stripPx: number) => void;
  end: () => void;
  cancel: () => void;
}

export function FocusStrip({
  window: w,
  events,
  region,
  shots,
  tFromBeep,
  readOnly,
  activeEdge,
  onEdge,
  onHandleDrag,
}: {
  window: FocusWindow;
  events: StageEvent[];
  region: StageEvent;
  shots: { shot_number: number; time_from_beep: number }[];
  tFromBeep: number;
  readOnly: boolean;
  activeEdge: "start" | "end" | null;
  onEdge: (edge: "start" | "end") => void;
  /** A handle dragged past the threshold: ``move`` gets the travel over the strip's width. */
  onHandleDrag?: HandleDrag;
}) {
  const stripRef = useRef<HTMLDivElement | null>(null);
  const press = useRef<{ id: number; x: number; edge: "start" | "end"; threshold: number; moved: boolean } | null>(null);
  // A press that moved still fires a click on release; that click is not a tap.
  const swallowClick = useRef(false);
  // At most six ruler labels: a phone's width holds no more side by side.
  const span = w.to - w.from;
  const step = [0.5, 1, 2, 5].find((s) => span / s <= 6) ?? 10;
  const ticks: number[] = [];
  for (let t = Math.ceil(w.from / step) * step; t <= w.to + 1e-9; t += step) ticks.push(t);
  const label = (t: number) => (step < 1 ? t.toFixed(1) : String(Math.round(t)));
  const inside = (t: number) => t >= w.from && t <= w.to;
  const neighbours = events.filter(
    (e) => e.kind === region.kind && e.id !== region.id && e.end >= w.from && e.start <= w.to,
  );
  const band = (e: StageEvent) => ({
    left: pct(fracOf(e.start, w)),
    width: pct(Math.max(fracOf(e.end, w) - fracOf(e.start, w), 0.005)),
  });
  return (
    <div
      ref={stripRef}
      data-testid="focus-strip"
      className="relative h-24 overflow-hidden rounded-md border border-rule bg-surface"
    >
      <div className="absolute inset-x-0 top-0 h-4 border-b border-rule">
        {ticks.map((t) => (
          <span
            key={t}
            data-ruler-label
            className="numeral absolute top-0 -translate-x-1/2 text-sm text-muted"
            style={{ left: pct(fracOf(t, w)) }}
          >
            {label(t)}
          </span>
        ))}
      </div>
      <div className="absolute inset-x-0 top-5 h-3">
        {shots
          .filter((s) => inside(s.time_from_beep))
          .map((s) => (
            <i
              key={s.shot_number}
              className="absolute top-0 h-3 w-0.5 bg-ink"
              style={{ left: pct(fracOf(s.time_from_beep, w)) }}
            />
          ))}
      </div>
      {neighbours.map((e) => (
        <div key={e.id} className="absolute top-10 h-10 rounded-sm bg-surface-2" style={band(e)} />
      ))}
      <div
        data-testid="focus-region"
        className={cn("absolute top-10 h-10 rounded-sm border-2", LANE_FILL[region.kind], region.source === "auto" && "border-dashed")}
        style={band(region)}
      />
      <div className="absolute bottom-0 top-4 w-0.5 bg-led" style={{ left: pct(fracOf(tFromBeep, w)) }} />
      {readOnly
        ? null
        : (["start", "end"] as const).map((edge) => {
            const t = edge === "start" ? region.start : region.end;
            return (
              <button
                key={edge}
                type="button"
                aria-label={`Adjust ${edge}, ${t.toFixed(2)} s`}
                aria-pressed={activeEdge === edge}
                onClick={() => {
                  if (swallowClick.current) {
                    swallowClick.current = false;
                    return;
                  }
                  onEdge(edge);
                }}
                onPointerDown={(e) => {
                  if (!onHandleDrag || press.current) return;
                  e.currentTarget.setPointerCapture(e.pointerId);
                  swallowClick.current = false;
                  const threshold = e.pointerType === "touch" ? TOUCH_DRAG_THRESHOLD_PX : DRAG_THRESHOLD_PX;
                  press.current = { id: e.pointerId, x: e.clientX, edge, threshold, moved: false };
                }}
                onPointerMove={(e) => {
                  const p = press.current;
                  if (!onHandleDrag || !p || p.id !== e.pointerId) return;
                  const dx = e.clientX - p.x;
                  if (!p.moved) {
                    if (Math.abs(dx) < p.threshold) return;
                    p.moved = true;
                    onHandleDrag.start(p.edge);
                  }
                  const width = Math.max(stripRef.current?.clientWidth ?? 0, 1);
                  onHandleDrag.move(dx / width, width);
                }}
                onPointerUp={(e) => {
                  const p = press.current;
                  if (!p || p.id !== e.pointerId) return;
                  press.current = null;
                  if (p.moved) {
                    swallowClick.current = true;
                    onHandleDrag?.end();
                  }
                }}
                onPointerCancel={(e) => {
                  const p = press.current;
                  if (!p || p.id !== e.pointerId) return;
                  press.current = null;
                  if (p.moved) onHandleDrag?.cancel();
                }}
                className="absolute top-9 flex h-12 w-11 -translate-x-1/2 touch-none items-center justify-center"
                style={{ left: pct(fracOf(t, w)) }}
              >
                <span className={cn("h-9 w-2.5 rounded-sm", activeEdge === edge ? "bg-led" : "bg-ink")} />
              </button>
            );
          })}
    </div>
  );
}
