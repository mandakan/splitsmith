/**
 * BeepReticle -- the touch beep picker. The waveform pans under a fixed
 * centre line and the time under the line is the pick: drag to pan,
 * pinch to zoom (about the line), the ruler says where you are, and the
 * overview strip below shows the whole snippet with the visible window
 * boxed so a beep outside the view is one tap away. Replaces a tap-to-
 * place strip that could neither scroll nor zoom. Geometry is in
 * lib/reticle.ts; this file draws and routes pointers.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  clamp,
  columnPeaks,
  initialPps,
  ppsBounds,
  ticks,
  visibleRange,
  xAtTime,
  type Range,
  type ReticleView,
} from "@/lib/reticle";

export interface ReticleMarker {
  time: number;
  kind: "detected" | "candidate";
}

export interface BeepReticleProps {
  peaks: number[];
  range: Range;
  /** Source time under the centre line. */
  value: number;
  onChange: (t: number) => void;
  markers: ReticleMarker[];
  /** Playback position while the snippet plays, else null. */
  playhead: number | null;
  /** Bumped by the parent to zoom in on ``value`` (a candidate jump). */
  focusSpan?: { span: number; nonce: number } | null;
  ariaLabel: string;
}

const MAIN_H = 132;
const RULER_H = 20;
const OVERVIEW_H = 32;
const ZOOM_STEP = 1.6;
const NUDGE_S = 0.01;

function useWidth() {
  const [width, setWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    if (!el) return;
    setWidth(Math.floor(el.getBoundingClientRect().width));
    observer.current = new ResizeObserver((entries) => {
      const w = Math.floor(entries[0]?.contentRect.width ?? 0);
      if (w > 0) setWidth(w);
    });
    observer.current.observe(el);
  }, []);
  return [ref, width] as const;
}

function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function prepCanvas(canvas: HTMLCanvasElement, width: number, height: number): CanvasRenderingContext2D | null {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return ctx;
}

function vline(ctx: CanvasRenderingContext2D, x: number, y0: number, y1: number, color: string, w: number, dash: number[] = []) {
  ctx.strokeStyle = color;
  ctx.lineWidth = w;
  ctx.setLineDash(dash);
  ctx.beginPath();
  ctx.moveTo(Math.round(x) + 0.5, y0);
  ctx.lineTo(Math.round(x) + 0.5, y1);
  ctx.stroke();
  ctx.setLineDash([]);
}

export function BeepReticle({ peaks, range, value, onChange, markers, playhead, focusSpan, ariaLabel }: BeepReticleProps) {
  const [wrapRef, width] = useWidth();
  const [pps, setPps] = useState<number | null>(null);
  const mainRef = useRef<HTMLCanvasElement | null>(null);
  const overviewRef = useRef<HTMLCanvasElement | null>(null);

  // Handlers read the latest centre and zoom from refs: several pointer
  // moves can land between renders, and each must build on the last.
  const centerRef = useRef(value);
  centerRef.current = value;
  const ppsRef = useRef(pps);
  ppsRef.current = pps;

  const bounds = useMemo(
    () => (width > 0 ? ppsBounds(width, { start: range.start, end: range.end }) : null),
    [width, range.start, range.end],
  );
  useEffect(() => {
    if (width > 0 && pps == null) setPps(initialPps(width, range));
  }, [width, pps, range]);
  useEffect(() => {
    if (!focusSpan || width <= 0) return;
    const b = ppsBounds(width, range);
    setPps(clamp(width / focusSpan.span, b.min, b.max));
    // The span is keyed by its nonce; range/width changes must not re-zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSpan?.nonce]);

  const setCenter = useCallback(
    (t: number) => {
      const c = clamp(t, range.start, range.end);
      centerRef.current = c;
      onChange(c);
    },
    [onChange, range.start, range.end],
  );
  const zoomBy = useCallback(
    (factor: number) => {
      if (!bounds) return;
      setPps((p) => clamp((p ?? bounds.min) * factor, bounds.min, bounds.max));
    },
    [bounds],
  );

  const view: ReticleView | null = pps != null && width > 0 ? { center: value, pps, width } : null;
  const ready = view != null;

  useLayoutEffect(() => {
    const canvas = mainRef.current;
    if (!canvas || !view) return;
    const ctx = prepCanvas(canvas, view.width, MAIN_H);
    if (!ctx) return;
    const waveTop = RULER_H;
    const waveH = MAIN_H - RULER_H;
    const mid = waveTop + waveH / 2;

    ctx.fillStyle = token("--color-surface-2", "#1a1a1a");
    const x0 = xAtTime(view, range.start);
    const x1 = xAtTime(view, range.end);
    if (x0 > 0) ctx.fillRect(0, waveTop, x0, waveH);
    if (x1 < view.width) ctx.fillRect(x1, waveTop, view.width - x1, waveH);

    ctx.fillStyle = token("--color-waveform-bar", "rgba(255,45,45,0.35)");
    const cols = columnPeaks(peaks, range, view);
    for (let c = 0; c < cols.length; c++) {
      const v = cols[c];
      if (v == null) continue;
      const h = Math.max(1, v * (waveH - 4));
      ctx.fillRect(c, mid - h / 2, 1, h);
    }

    const beep = token("--color-waveform-beep", "#06B6D4");
    const muted = token("--color-muted", "#888");
    for (const m of markers) {
      const x = xAtTime(view, m.time);
      if (x < -2 || x > view.width + 2) continue;
      if (m.kind === "detected") vline(ctx, x, waveTop, MAIN_H, beep, 1.5, [4, 3]);
      else vline(ctx, x, waveTop, MAIN_H, muted, 1, [2, 3]);
    }
    if (playhead != null) {
      const x = xAtTime(view, playhead);
      if (x >= 0 && x <= view.width) vline(ctx, x, waveTop, MAIN_H, token("--color-ink-2", "#ccc"), 1);
    }

    ctx.fillStyle = token("--color-bg", "#000");
    ctx.fillRect(0, 0, view.width, RULER_H);
    const rule = token("--color-rule-strong", "#444");
    ctx.fillStyle = rule;
    ctx.fillRect(0, RULER_H - 1, view.width, 1);
    ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textBaseline = "top";
    for (const t of ticks(view, range)) {
      const major = t.label != null;
      vline(ctx, t.x, major ? RULER_H - 8 : RULER_H - 4, RULER_H - 1, major ? muted : rule, 1);
      if (major) {
        ctx.fillStyle = muted;
        ctx.fillText(t.label!, t.x + 3, 3);
      }
    }

    vline(ctx, view.width / 2, 0, MAIN_H, token("--color-led", "#FF2D2D"), 2);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- view is rebuilt every render from these
  }, [value, pps, width, peaks, range, markers, playhead]);

  useLayoutEffect(() => {
    const canvas = overviewRef.current;
    if (!canvas || !view) return;
    const ctx = prepCanvas(canvas, view.width, OVERVIEW_H);
    if (!ctx) return;
    const fit: ReticleView = { center: (range.start + range.end) / 2, pps: view.width / (range.end - range.start), width: view.width };
    const mid = OVERVIEW_H / 2;
    ctx.fillStyle = token("--color-waveform-bar", "rgba(255,45,45,0.35)");
    const cols = columnPeaks(peaks, range, fit);
    for (let c = 0; c < cols.length; c++) {
      const v = cols[c];
      if (v == null) continue;
      const h = Math.max(1, v * (OVERVIEW_H - 4));
      ctx.fillRect(c, mid - h / 2, 1, h);
    }
    const beep = token("--color-waveform-beep", "#06B6D4");
    const muted = token("--color-muted", "#888");
    for (const m of markers) {
      vline(ctx, xAtTime(fit, m.time), 0, OVERVIEW_H, m.kind === "detected" ? beep : muted, 1);
    }
    const vis = visibleRange(view);
    const bx0 = xAtTime(fit, Math.max(vis.start, range.start));
    const bx1 = xAtTime(fit, Math.min(vis.end, range.end));
    ctx.strokeStyle = token("--color-ink-2", "#ccc");
    ctx.lineWidth = 1;
    ctx.strokeRect(Math.round(bx0) + 0.5, 0.5, Math.max(2, bx1 - bx0 - 1), OVERVIEW_H - 1);
    vline(ctx, xAtTime(fit, view.center), 0, OVERVIEW_H, token("--color-led", "#FF2D2D"), 1.5);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- view is rebuilt every render from these
  }, [value, pps, width, peaks, range, markers]);

  // --- gestures on the main strip: 1 pointer pans, 2 pinch-zoom + pan ---
  const pointers = useRef(new Map<number, number>());
  // A pinch is anchored to its starting state, so zoom and mid-point pan
  // compose without drift however many moves it takes.
  const pinch = useRef<{ dist: number; mid: number; pps: number; center: number } | null>(null);

  const pinchState = () => {
    const xs = [...pointers.current.values()];
    return { dist: Math.max(8, Math.abs(xs[0] - xs[1])), mid: (xs[0] + xs[1]) / 2 };
  };
  const onPointerDown = (e: PointerEvent<HTMLCanvasElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, e.clientX);
    if (pointers.current.size === 2 && ppsRef.current != null)
      pinch.current = { ...pinchState(), pps: ppsRef.current, center: centerRef.current };
  };
  const onPointerMove = (e: PointerEvent<HTMLCanvasElement>) => {
    const prev = pointers.current.get(e.pointerId);
    const p = ppsRef.current;
    if (prev == null || p == null) return;
    if (pointers.current.size === 1) {
      pointers.current.set(e.pointerId, e.clientX);
      setCenter(centerRef.current - (e.clientX - prev) / p);
      return;
    }
    pointers.current.set(e.pointerId, e.clientX);
    const now = pinchState();
    const start = pinch.current;
    if (start && bounds) {
      const next = clamp((start.pps * now.dist) / start.dist, bounds.min, bounds.max);
      ppsRef.current = next;
      setPps(next);
      setCenter(start.center - (now.mid - start.mid) / next);
    }
  };
  const onPointerEnd = (e: PointerEvent<HTMLCanvasElement>) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
  };

  // Trackpad / mouse wheel, so the picker is usable when testing on a
  // desktop browser too. Non-passive: the page must not scroll with it.
  useEffect(() => {
    const canvas = mainRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      const p = ppsRef.current;
      if (p == null) return;
      e.preventDefault();
      if (e.ctrlKey) zoomBy(Math.exp(-e.deltaY / 100));
      else setCenter(centerRef.current + (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) / p);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [zoomBy, setCenter, ready]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? NUDGE_S * 10 : NUDGE_S;
    if (e.key === "ArrowLeft") setCenter(value - step);
    else if (e.key === "ArrowRight") setCenter(value + step);
    else if (e.key === "+" || e.key === "=") zoomBy(ZOOM_STEP);
    else if (e.key === "-") zoomBy(1 / ZOOM_STEP);
    else return;
    e.preventDefault();
  };

  // --- overview: tap or drag jumps the centre there ---
  const overviewJump = (e: PointerEvent<HTMLCanvasElement>) => {
    if (!view) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const fitPps = view.width / (range.end - range.start);
    setCenter(range.start + (e.clientX - rect.left) / fitPps);
  };

  const span = view ? view.width / view.pps : null;

  return (
    <div className="space-y-2">
      <div
        ref={wrapRef}
        role="slider"
        tabIndex={0}
        aria-label={ariaLabel}
        aria-valuemin={range.start}
        aria-valuemax={range.end}
        aria-valuenow={value}
        aria-valuetext={`${value.toFixed(3)} seconds`}
        onKeyDown={onKeyDown}
        className="overflow-hidden rounded-md border border-rule bg-bg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
      >
        <canvas
          ref={mainRef}
          data-testid="reticle-main"
          className="block touch-none"
          style={{ height: MAIN_H }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd}
          onPointerCancel={onPointerEnd}
        />
      </div>
      <canvas
        ref={overviewRef}
        data-testid="reticle-overview"
        aria-hidden
        className="block w-full touch-none rounded-sm border border-rule bg-bg"
        style={{ height: OVERVIEW_H }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          overviewJump(e);
        }}
        onPointerMove={(e) => {
          if (e.currentTarget.hasPointerCapture(e.pointerId)) overviewJump(e);
        }}
      />
      <div className="flex items-center gap-2">
        <Button type="button" className="h-11 w-11 p-0 text-md" onClick={() => zoomBy(1 / ZOOM_STEP)} disabled={!bounds || pps == null || pps <= bounds.min} aria-label="Zoom out">
          &minus;
        </Button>
        <Button type="button" className="h-11 w-11 p-0 text-md" onClick={() => zoomBy(ZOOM_STEP)} disabled={!bounds || pps == null || pps >= bounds.max} aria-label="Zoom in">
          +
        </Button>
        <Button type="button" className="h-11" onClick={() => bounds && setPps(bounds.min)} disabled={!bounds || pps == null || pps <= bounds.min}>
          Whole clip
        </Button>
        <span className="numeral ml-auto text-sm text-muted">{span != null ? `${span < 1 ? span.toFixed(2) : span.toFixed(1)} s across` : ""}</span>
      </div>
    </div>
  );
}
