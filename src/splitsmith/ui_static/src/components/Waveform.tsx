/**
 * Static-peaks waveform with pointer-driven scrubbing + horizontal zoom.
 *
 * Issue #15: the audit screen renders the primary's audio as a static
 * canvas and overlays the active video's playhead. Scrubbing the
 * waveform updates playback time directly -- no two-element sync drift.
 *
 * Two layout modes:
 *   - **fit**: ``pixelsPerSecond`` is null/undefined; content width tracks
 *     the container, no horizontal scroll. The default.
 *   - **zoom**: ``pixelsPerSecond`` set; content width = duration * pps,
 *     wrapped in an overflow-x-auto outer; auto-scrolls the playhead
 *     into view during playback.
 *
 * Marker children compose into the inner (content) div so their
 * absolute / percent positions stay correct under both modes.
 *
 * Contract:
 *   - `peaks` is server-computed (splitsmith.waveform.compute_peaks).
 *   - `currentTime` drives the playhead; the parent feeds it from the
 *     active <video> via rAF.
 *   - `onScrub(t)` fires while the user drags; rAF-throttled internally.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";

interface WaveformProps {
  peaks: number[];
  duration: number;
  currentTime: number;
  onScrub: (timeSeconds: number) => void;
  onScrubEnd?: () => void;
  /** Fires on a double-click on the waveform background. The parent can
   *  use this to add a manual marker at the clicked time (issue #15). */
  onDoubleClick?: (timeSeconds: number) => void;
  beepTime?: number | null;
  /** Pixels-per-second of the rendered content. Null/undefined => fit
   *  the visible container width (no horizontal scroll). */
  pixelsPerSecond?: number | null;
  height?: number;
  className?: string;
  ariaLabel?: string;
  children?: React.ReactNode;
}

export function Waveform({
  peaks,
  duration,
  currentTime,
  onScrub,
  onScrubEnd,
  onDoubleClick,
  beepTime,
  pixelsPerSecond,
  height = 128,
  className,
  ariaLabel = "Audio waveform -- drag to scrub",
  children,
}: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const innerRef = useRef<HTMLDivElement | null>(null);
  const outerRef = useRef<HTMLDivElement | null>(null);
  // Tracks the visible viewport's width so fit-mode can size the canvas
  // and zoom-mode can know when the playhead leaves the visible area.
  const [viewportWidth, setViewportWidth] = useState(0);
  const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
  const draggingRef = useRef(false);
  const pendingTimeRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);

  useLayoutEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 0) setViewportWidth(w);
      }
    });
    observer.observe(el);
    setViewportWidth(Math.floor(el.getBoundingClientRect().width));
    return () => observer.disconnect();
  }, []);

  // Effective content width in CSS pixels.
  const contentWidth = useMemo(() => {
    if (pixelsPerSecond != null && duration > 0) {
      return Math.max(1, Math.floor(pixelsPerSecond * duration));
    }
    return viewportWidth;
  }, [pixelsPerSecond, duration, viewportWidth]);

  const cssVar = useCallback((name: string, fallback: string) => {
    if (typeof window === "undefined") return fallback;
    const root = document.documentElement;
    const value = getComputedStyle(root).getPropertyValue(name).trim();
    return value || fallback;
  }, []);

  // Draw bars + playhead + optional beep marker.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || contentWidth === 0) return;

    const cssWidth = contentWidth;
    const cssHeight = height;
    canvas.width = Math.floor(cssWidth * dpr);
    canvas.height = Math.floor(cssHeight * dpr);
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${cssHeight}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);

    const barColor = cssVar("--waveform-bar", "#888");
    const playheadColor = cssVar("--waveform-playhead", "#d55e00");
    const beepColor = cssVar("--waveform-beep", "#0072b2");

    // Bars: one per peak, scaled to content width.
    const n = peaks.length;
    if (n > 0) {
      const barStride = cssWidth / n;
      const barWidth = Math.max(1, barStride * 0.9);
      const halfH = cssHeight / 2;
      ctx.fillStyle = barColor;
      for (let i = 0; i < n; i++) {
        const p = peaks[i];
        const h = Math.max(1, p * (cssHeight - 2));
        const x = i * barStride + (barStride - barWidth) / 2;
        ctx.fillRect(x, halfH - h / 2, barWidth, h);
      }
    }

    if (beepTime != null && duration > 0 && beepTime >= 0 && beepTime <= duration) {
      const x = (beepTime / duration) * cssWidth;
      ctx.strokeStyle = beepColor;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(x + 0.5, 0);
      ctx.lineTo(x + 0.5, cssHeight);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if (duration > 0) {
      const x = (Math.min(Math.max(currentTime, 0), duration) / duration) * cssWidth;
      ctx.strokeStyle = playheadColor;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, cssHeight);
      ctx.stroke();
    }
  }, [peaks, duration, currentTime, beepTime, contentWidth, height, dpr, cssVar]);

  // Auto-scroll the playhead into view during playback. Edge-trigger:
  // only adjust scroll when the playhead leaves a center band, otherwise
  // small playback jitter would yank the user's manual scrolling.
  useEffect(() => {
    const outer = outerRef.current;
    if (!outer || contentWidth <= viewportWidth || duration <= 0) return;
    if (draggingRef.current) return; // user is scrubbing; don't fight them
    const playheadX = (Math.min(Math.max(currentTime, 0), duration) / duration) * contentWidth;
    const visibleLeft = outer.scrollLeft;
    const visibleRight = visibleLeft + viewportWidth;
    const margin = viewportWidth * 0.1;
    if (playheadX < visibleLeft + margin || playheadX > visibleRight - margin) {
      outer.scrollLeft = Math.max(0, playheadX - viewportWidth / 2);
    }
  }, [currentTime, contentWidth, viewportWidth, duration]);

  const flushScrub = useCallback(() => {
    rafRef.current = null;
    const t = pendingTimeRef.current;
    if (t != null) {
      pendingTimeRef.current = null;
      onScrub(t);
    }
  }, [onScrub]);

  const queueScrub = useCallback(
    (t: number) => {
      pendingTimeRef.current = t;
      if (rafRef.current == null) {
        rafRef.current = requestAnimationFrame(flushScrub);
      }
    },
    [flushScrub],
  );

  // Pointer math: clientX -> time. Uses the inner content div's bounding
  // rect (which reflects scroll position automatically), so zoom + scroll
  // don't break scrub accuracy.
  const timeFromEvent = useCallback(
    (clientX: number): number => {
      const el = innerRef.current;
      if (!el || duration <= 0) return 0;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0) return 0;
      const ratio = (clientX - rect.left) / rect.width;
      return Math.min(Math.max(ratio, 0), 1) * duration;
    },
    [duration],
  );

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const el = innerRef.current;
      if (!el) return;
      el.setPointerCapture(e.pointerId);
      draggingRef.current = true;
      queueScrub(timeFromEvent(e.clientX));
    },
    [queueScrub, timeFromEvent],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingRef.current) return;
      queueScrub(timeFromEvent(e.clientX));
    },
    [queueScrub, timeFromEvent],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      const el = innerRef.current;
      if (el && el.hasPointerCapture(e.pointerId)) {
        el.releasePointerCapture(e.pointerId);
      }
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
        flushScrub();
      }
      onScrubEnd?.();
    },
    [flushScrub, onScrubEnd],
  );

  useEffect(() => {
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const ariaValueText = useMemo(() => {
    if (duration <= 0) return "0:00 of 0:00";
    return `${formatTime(currentTime)} of ${formatTime(duration)}`;
  }, [currentTime, duration]);

  return (
    <div
      ref={outerRef}
      className={cn(
        "relative w-full select-none rounded-md bg-muted/40 ring-1 ring-border",
        "overflow-x-auto overflow-y-hidden",
        className,
      )}
      style={{ height }}
    >
      <div
        ref={innerRef}
        role="slider"
        aria-label={ariaLabel}
        aria-valuemin={0}
        aria-valuemax={Math.max(duration, 0)}
        aria-valuenow={Math.min(Math.max(currentTime, 0), Math.max(duration, 0))}
        aria-valuetext={ariaValueText}
        tabIndex={0}
        className={cn(
          "relative cursor-ew-resize touch-none",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
        style={{ width: contentWidth, height }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onDoubleClick={(e) => {
          if (!onDoubleClick) return;
          if ((e.target as HTMLElement).closest("[data-audit-marker]")) return;
          onDoubleClick(timeFromEvent(e.clientX));
        }}
      >
        <canvas ref={canvasRef} className="block" />
        {children}
      </div>
    </div>
  );
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  const ms = Math.floor((seconds - total) * 1000);
  return `${m}:${s.toString().padStart(2, "0")}.${ms.toString().padStart(3, "0")}`;
}
